#!/usr/bin/env python
"""
Copyright (C) 2021 Antonio Tejada

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.



https://github.com/micropython/micropython-lib/blob/master/micropython/umqtt.simple/umqtt/simple.py


Some topic design best practices
https://www.hivemq.com/blog/mqtt-essentials-part-5-mqtt-topics-best-practices/

mqtt and websockets

enable both tcp and websocket listeners in mosquitto.conf with
    # this will listen for mqtt on tcp
    listener 1883

    # this will listen for websockets on 9001
    listener 9001
    protocol websockets

http://www.steves-internet-guide.com/mqtt-websockets/
http://www.steves-internet-guide.com/using-javascript-mqtt-client-websockets/


https://randomnerdtutorials.com/micropython-mqtt-esp32-esp8266/
"""

import dht
import errno
import gc
import machine
import os
import time
import ujson as json
import ubinascii as binascii

from config import read_config, write_config
from logging import log_info, log_exception
from mqtt import mqtt_create, mqtt_connect, mqtt_publish_message, mqtt_publish_state_message, get_epoch, mqtt_check_msg, mqtt_disconnect
from syncedtime import sync_time_with_ntp, get_epoch

# Test reception e.g. with:
# mosquitto_sub -t foo_topic

# suscribe thermostat ctrl
# - turn fan on (set a safety max time?)
# - turn fan off
# - turn ac on (will turn fan on too) (set a safety max time?)
# - turn ac off (will turn fan off too)
# - turn fan on for a period of time
# - turn ac on for a period of time
# - XXX turn ac on at a temperature once DHT22 is supported
# - XXX Schedules?

# publish ac topic
# - fan starts
# - fan stops
# - ac starts
# - ac stops
# - XXX temperature when it changes above some delta/every min once DHT22 is supported

# Safety rules:
# - always start the fan when AC is started
# - always start the fan first a few seconds before AC
# - wait a safety interval (1min?) since the last AC stop
# - don't run AC for more than 30 mins straight
# - don't allow setting temps below 70F
# - when following a temp, start the AC when it's above the temp plus safety margin, stop when it's below the temp minus safety margin
    
config_filename = "lessmostat.cfg"

def sub_cb(client, topic, msg):
    """

    Start at the given temp or higher
    { "state" : "on", temp="26" }

    Set fan to "auto" or "on"
    { "state" : "auto" }
    { "state" : "on" }
    

    XXX TODO complex scheduling
    messages
    topic: x/lessmostat/control/fan msg: { timestamp: , state: "on" | "off",  tod :, dow , temp : degs }
    topic: x/lessmostat/control/ac msg: { timestamp: , state: "on" | "off", tod :,  dow, temp : degs }
    topic: x/lessmostat/control/reset:
    topic: x/lessmostat/control/state: force state publish

    state: "on" | "off" set the device to on or off
    start_dow : start day of the week from 0 to 6, null for today
    start_time : 24 hour and minute start time, null for now
    start_temp : null for any temp

    { "state": "on" } Start 
    { "state": "off" } Stop

    Start at the given temp or higher
    { "state" : "on", temp="26" }
    Stop at the given temp or lower
    { "state" : "off", temp="24" }

    Run for one hour
    { "state" : "on", tod: now() }
    { "state" : "off", tod: now()+1 hour }

    set morning and evening temps
    { "state" : "on", tod: 10am, temp="26" }
    { "state" : "off", tod: 8pm, temp="24" }
    { "state" : "on", tod: 8pm, temp="28" }
    { "state" : "off", tod: 10am, temp="26" }

    Stop on sundays 10am
    { "state" : "off", tod:10am, dow:6 }

    The controller will impose safety limits and then resume when the safety stop has been done:
    - ac run less than 30 mins straight
    - ac run with 5 mins between stop / start
    - fan run less than 12 hours straight
    - fan run with 1 mins between stop / start

    sunrise and sunset 

    https://sunrise-sunset.org/api
    https://stackoverflow.com/questions/19615350/calculate-sunrise-and-sunset-times-for-a-given-gps-coordinate-within-postgresql

    request = Request('http://api.sunrise-sunset.org/json?lat=25.7741728&lng=-80.19362&formatted=0')
    response = urlopen(request)
    timestring = response.read()

    utcsunrise = timestring[34:39]
    utcsunset = timestring[71:76]
    utcmorning = timestring[182:187]
    utcnight = timestring[231:236]

    Get lat/long from https://sunrise-sunset.org/search?location=miami
    """
    log_info("callback for topic %r msg %r" % (topic, msg))

    try:
        d = json.loads(msg)

        if (topic.endswith("/state")):
            mqtt_publish_state_message(client, state)

        elif (topic.endswith("control/ac")):
            state["config"]["ac_rules"] = [
                { "state" : "on", "temp" : d["temp"], "humid" : d["humid"] },
            ]
            # XXX Should this publish only the delta?
            mqtt_publish_state_message(client, state)

        elif (topic.endswith("control/heat")):
            state["config"]["heat_rules"] = [
                { "state" : "on", "temp" : d["temp"], "humid" : d["humid"] },
            ]
            # XXX Should this publish only the delta?
            mqtt_publish_state_message(client, state)

        elif (topic.endswith("control/fan")):
            state["config"]["fan_rules"] = [
                { "state" : d["state"] },
            ]
            # XXX Should this publish only the delta?
            mqtt_publish_state_message(client, state)

        elif (topic.endswith("control/store_preset")):
            # Copy the current rules into the given preset
            preset_index = max(0, min(d["index"], max_presets - 1))
            # XXX What about fan and heat vs. cooling? does the fan need to be
            #     moved inside heating/cooling?
            state["config"]["presets"][preset_index]["fan"] = state["config"]["fan_rules"][0]
            if (d["mode"] == "cooling"):
                state["config"]["presets"][preset_index]["ac"] = state["config"]["ac_rules"][0]
            else:
                state["config"]["presets"][preset_index]["heat"] = state["config"]["heat_rules"][0]

            # XXX Should this publish only the delta?
            mqtt_publish_state_message(client, state)
            
            write_config(config_filename, state)

    except Exception as e:
        log_exception("Exception handling topic %r message %r" % (topic, msg), e)

# See 
# http://www.icstation.com/esp8266-wifi-channel-relay-module-smart-home-remote-control-switch-android-phone-control-transmission-distance-100m-p-12592.html
# https://www.icstation.com/esp8266-wifi-channel-relay-module-remote-control-switch-wireless-transmitter-smart-home-p-13420.html
relay_1_off = [0xA0, 0x01, 0x00, 0xA1]
relay_1_on = [0xA0, 0x01, 0x1, 0xA2]
relay_2_off = [0xA0, 0x02, 0x00, 0xA2]
relay_2_on = [0xA0, 0x02, 0x01, 0xA3]
relay_3_off = [0xA0, 0x03, 0x00, 0xA3]
relay_3_on = [0xA0, 0x03, 0x01, 0xA4]
relay_4_off = [0xA0, 0x04, 0x00, 0xA4]
relay_4_on = [0xA0, 0x04, 0x01, 0xA5]
        
def uart_write(uart, b):
    uart.write(b)
    # XXX Looks like some delay is needed between uart writes otherwise the 
    #     second write is lost and the relay state is not modified
    #     This is the case even with some code between uart_write invocations
    time.sleep(1)

fan_on = bytes(relay_2_on)
fan_off = bytes(relay_2_off)
def turn_fan(uart, client, on):
    fan_state = state["fan"]
    # Uptime accumulation assumes there are no redundant calls
    assert ((on and (fan_state != "on")) or ((not on) and fan_state != "off"))
    if (on):
        # XXX Should prevent somewhere it's not trying to re-enable fan before the
        #     safety idle period
        uart_write(uart, fan_on)
        fan_state = "on"

    else:
        uart_write(uart, fan_off)
        fan_state = "off"
        
    state["fan"] = fan_state
    prev_fan_mod_ts = state["fan_mod_ts"]
    state["fan_mod_ts"] = get_epoch()
    if (not on):
        # Accumulate uptime
        state["fan_uptime"] += (state["fan_mod_ts"] - prev_fan_mod_ts)

    mqtt_publish_message(client, "info/fan", { 'state' : fan_state, 'mod_ts' : state["fan_mod_ts"], 'uptime' : state["fan_uptime"] })

ac_on = bytes(relay_1_on)
ac_off = bytes(relay_1_off)
heat_on = bytes(relay_3_on)
heat_off = bytes(relay_3_off)
def turn_ac_heat(uart, client, ac_heat, on):
    """
    @param ac_heat one of "ac" or "heat"
    """
    ac_heat_state = state[ac_heat]
    other_ac_heat = "heat" if (ac_heat == "ac") else "ac"
    # Never turn on heat if ac is on or viceversa
    if ((state[other_ac_heat] == "on") and on):
        log_info("Ignoring turning %s on when %s is already on" % (ac_heat, other_ac_heat))
        return

    # Uptime accumulation assumes there are no redundant calls
    assert ((on and (ac_heat_state != "on")) or ((not on) and (ac_heat_state != "off")))
    if (on):
        # Always turn fan on before ac/heat
        if (state["fan"] != "on"):
            log_info("%s forcing fan on" % ac_heat)
            turn_fan(uart, client, on)

        # XXX Should prevent somewhere it's not trying to re-enable ac
        #     before the safety idle period

        uart_write(uart, ac_on if (ac_heat == "ac") else heat_on)
        ac_heat_state = "on"
        
    else:
        uart_write(uart, ac_off if (ac_heat == "ac") else heat_off)
        ac_heat_state = "off"
        
        # Leave the fan on, let it turn off depending on the rules

    state[ac_heat] = ac_heat_state
    prev_ac_heat_mod_ts= state["%s_mod_ts" % ac_heat]
    state["%s_mod_ts" % ac_heat] = get_epoch()
    if (not on):
        # Accumulate uptime
        state["%s_uptime" % ac_heat] += (state["%s_mod_ts" % ac_heat] - prev_ac_heat_mod_ts)

    mqtt_publish_message(client, "info/%s" % ac_heat, { 'state' : ac_heat_state, 'mod_ts' : state["%s_mod_ts" % ac_heat], 'uptime' : state["%s_uptime" % ac_heat] })

state = { 
    # Current state
    "start_ts" : None,

    "ac" : "off",
    "ac_mod_ts" : None,
    "ac_uptime" : 0,

    "heat" : "off",
    "heat_mod_ts" : None,
    "heat_uptime" : 0,

    "fan" : "off",
    "fan_mod_ts" : None,
    "fan_uptime" : 0,

    "sensor" : { "humid" : None, "temp" : None },

    
    # Configuration state
    # XXX This may want to save the mod_ts so it doesn't continuously 
    #     restart the AC. Alternatively always wait the safety period when 
    #     restarting the program
    "config" : {
        "mqtt_broker" : "192.168.8.201",
        "mqtt_topic" : "apartment/lessmostat/",
        # The AC has a single rule which is to 
        # - in cooling mode, start at the given temperature + hi_threshold and
        #   stop at the temperature - lo_threshold
        # - in heating mode, start at the given temperature - lo_threshold and
        #   stop at the temperature + hi_threshold
        "ac_rules" : [
            { "state" : "on", "temp" : 30, "humid" : 70 },
        ],
        "heat_rules" : [
            { "state" : "on", "temp" : 10, "humid" : 70 },
        ],
        # The fan has a single rule which is to set the fan to "on" (always
        # on or "auto" (match ac state)
        # XXX Allow setting the fan on a timer ("on", 15-30-60-120 min, "auto")
        #     and then revert to auto
        # XXX The fan counter could also show a guess on how long it will take
        #     to bring to the desired temp. Or move to a counter in the
        #     idle/cooling/heating state
        "fan_rules" : [
            { "state" : "auto" }
        ],
        # Hi and low thresholds in 1/10th of a celsius degree (ie in cooling
        # mode will turn on at temp + hi threshold and off at temp - low
        # threshold, in heating will turn on at temp - low and off at temp + hi)
        "lo_threshold_decidegs" : 4, 
        "hi_threshold_decidegs" : 4,

        # Hi and low thresholds in 1/10th of a humidity percentage (ie will turn
        # on at humid + hi threshold and off at humid - low threshold
        "lo_threshold_decihumids" : 40,
        "hi_threshold_decihumids" : 40,

        # XXX Should this store the thresholds too?
        "presets" : [
            { "fan" : { "state" : "auto" }, "heat" : { "state" : "on", "temp" : 10, "humid" : 70 }, "ac" : { "state" : "on", "temp" : 30, "humid" : 70 } },
            { "fan" : { "state" : "auto" }, "heat" : { "state" : "on", "temp" : 10, "humid" : 70 }, "ac" : { "state" : "on", "temp" : 30, "humid" : 70 } },
            { "fan" : { "state" : "auto" }, "heat" : { "state" : "on", "temp" : 10, "humid" : 70 }, "ac" : { "state" : "on", "temp" : 30, "humid" : 70 } },
        ],
    }
}
max_presets = len(state["config"]["presets"])

def main():
    try:
        log_info("Reading initial configuration")
        read_config(config_filename, state)
        log_info("Initial state is %r" % state)

        # Fetch some constant values from the config
        hi_threshold_decidegs = state["config"]["hi_threshold_decidegs"]
        lo_threshold_decidegs = state["config"]["lo_threshold_decidegs"]
        hi_threshold_decihumids = state["config"]["hi_threshold_decihumids"]
        lo_threshold_decihumids = state["config"]["lo_threshold_decihumids"]
        mqtt_broker = state["config"]["mqtt_broker"]
        mqtt_topic = state["config"]["mqtt_topic"]

        # Update time with NTP
        sync_time_with_ntp()
        
        client_id = binascii.hexlify(machine.unique_id())

        # Now that we have an NTP time, initialize state times
        now_ts = get_epoch()
        state["start_ts"] = now_ts
        state["ac_mod_ts"] = now_ts
        state["fan_mod_ts"] = now_ts
        
        log_info("Initializing relays uart")
        uart = machine.UART(0, baudrate=115200, bits=8, parity=None, stop=1)
        # Detach REPL from UART so UART can be used for the relays
        log_info("Detaching UART from repl")
        os.dupterm(uart, 1)

        # The DHT is connected to the 5V, GND and RX (gpio 3)
        # Steal the RX pin from the uart, see
        # https://forum.micropython.org/viewtopic.php?t=6669
        log_info("Initializing DHT sensor")
        dht_sensor = dht.DHT22(machine.Pin(3, machine.Pin.IN))
        # DHT22 is known to timeout the first few times measure() is called, retry
        num_retries = 10
        while (num_retries > 0):
            try:
                num_retries -= 1
                dht_sensor.measure()
                break
            except OSError as e:
                if (e.errno == errno.ETIMEDOUT):
                    log_info("DHT sensor timed out, retrying")

                else:
                    raise

        # On power unplug reset the relays are closed, but on machine reset they
        # are left to whatever state before reset, so set the AC and fan relays
        # to a known state (closed)
        # XXX Should this have some hysteresis in case this is always starting
        #     and engaging the ac/fan? It already has a delay in main.py, but
        #     the main loop could have a warm-up timer where it ignores the
        #     rules (or just a plain sleep)
        log_info("Setting ac to known state")
        uart_write(uart, ac_off)
        log_info("Setting heat to known state")
        uart_write(uart, heat_off)
        log_info("Setting fan to known state")
        uart_write(uart, fan_off)

        client = mqtt_create(mqtt_broker, client_id, mqtt_topic, sub_cb)
        # Now that the state is known, connect and accept control commands
        mqtt_connect(client)

        # Advertise the initial state after accepting control commands so
        # clients can start as soon as they get the initial state
        # XXX There's the theoretical possibility of a stale control command 
        #     coming in before the state is advertised?
        mqtt_publish_state_message(client, state)
        
        log_info("Starting sensor reading and MQTT message handling forever loop")
        while (True):
            # Doing a GC here seems to help random restarts without registering
            # an exception, probably caused by logging code causing out of
            # memory errors (which would explain why no exception is logged)
            gc.collect()

            # Gather sensor information
            # Do sparingly since this can take 2s on DHT22, 1s on DHT11
            dht_sensor.measure()

            temp = dht_sensor.temperature()
            humid = dht_sensor.humidity()

            # Publish sensor information
            # XXX Should this only publish changes?
            mqtt_publish_message(client, "info/sensor", {'temp' : temp, 'humid' : humid})

            state["sensor"]["temp"] = temp
            state["sensor"]["humid"] = humid

            sync_time_with_ntp()
            
            # Wait some seconds between reporting sensor data (the wait could be
            # longer depending on the execution time of the loop below, but it's
            # okay even if we ever need some time accurate policy because sensor
            # messages contain timestamp)
            sleep_ms = 10000
            # Sleep a few millis between message checks so target temperature
            # updates on the client are responsive
            sleep_iteration_ms = 500
            iterations = sleep_ms / sleep_iteration_ms
            for i in range(iterations):
                # Non-blocking check for messages
                
                try:
                    # This raises OSERROR (-1), ECONNRESET (errno 114),
                    # ECONNABORTED (errno 103) on error
                    # Also returns EHOSTUNREACH (errno 113) if it was never able
                    # to connect
                    mqtt_check_msg(client)

                except OSError as e:
                    # Don't bother logging these to file as they are too noisy
                    # and non fatal
                    log_exception("Exception checking MQTT message", e, True)
                    
                    mqtt_connect(client)

                time.sleep_ms(sleep_iteration_ms)

                # Check rules
                # Collect active rules
                # No need to refresh temp, will be refreshed in the next 30s
                # XXX This only needs checking if the rules changed, since the
                #     temperature is only updated above
                ac_rules = state["config"]["ac_rules"]
                heat_rules = state["config"]["heat_rules"]
                # XXX Note heating/cooling assumes the right wire (white heating
                #     / yellow for cooling) is being driven by the ac_on relay.
                #     Ideally this should use a three or four-channel relay,
                #     another option is to connect the green wire (fan) to both,
                #     drive heating with the current fan relay the and lose
                #     independent fan control?
                for rule in ac_rules + heat_rules:
                    rule_state = rule["state"]
                    rule_temp = rule.get("temp", None)
                    # XXX In heating mode the AC seems to wait for around one
                    #     minute to turn the fan off, there should be a way to
                    #     put that safety rule
                    
                    # XXX This will turn heating on in the same iteration ac is
                    #     turned off and without intervening fan turning off,
                    #     should wait some time? (for the converse, ac & fan are
                    #     not turned on until the next iteration because of the
                    #     way heat_rules are processed after ac_rules in the for
                    #     rule iteration above)

                    heating = rule in heat_rules
                    cooling = not heating
                    turn_ac_heat_on_count = 0
                    turn_ac_heat_off_count = 0
                    ac_heat_state = state["ac" if cooling else "heat"]
                    ac_heat = "ac" if cooling else "heat"
                    if (rule_temp is not None):
                        under_threshold = (temp*10 <= rule_temp*10 - lo_threshold_decidegs)
                        over_threshold = (temp*10 >= rule_temp*10 + hi_threshold_decidegs)
                        if ((ac_heat_state != "on") and (rule_state == "on") and 
                            ((heating and under_threshold) or (cooling and over_threshold))):
                            turn_ac_heat_on_count += 1
                            
                        elif ((ac_heat_state != "off") and (rule_state == "on") and 
                            ((heating and over_threshold) or (cooling and under_threshold))):
                            turn_ac_heat_off_count += 1
                            
                    rule_humid = rule.get("humid", None)
                    if (rule_humid is not None):
                        under_threshold = (humid*10 <= rule_humid*10 - lo_threshold_decihumids)
                        over_threshold = (humid*10 >= rule_humid*10 + hi_threshold_decihumids)

                        if ((ac_heat_state != "on") and (rule_state == "on") and over_threshold):
                            turn_ac_heat_on_count +=1
                            
                        elif ((ac_heat_state != "off") and (rule_state == "on") and under_threshold):
                            turn_ac_heat_off_count += 1

                    # Turn AC off if any of temp or humid require it, turn off
                    # if both temp and humid require it
                    #
                    # Note that due how thresholds work it's possible that the
                    # AC gets turned on because of temp, but once below the temp
                    # threshold it's kept on because of not being below the
                    # humid threshold. This seems ok even if non-obvious, other
                    # option would be to keep track of the rule that enabled the
                    # ac and only allow that one to keep it on, but would
                    # complicate the logic for little benefit?
                    
                    if (turn_ac_heat_on_count >= 1):
                        log_info("Starting %s, on %d off %d" % (ac_heat, turn_ac_heat_on_count, turn_ac_heat_off_count))
                        turn_ac_heat(uart, client, ac_heat, True)

                    elif (turn_ac_heat_off_count == 2):
                        log_info("Stopping %s, on %d off %d" % (ac_heat, turn_ac_heat_on_count, turn_ac_heat_off_count))
                        turn_ac_heat(uart, client, ac_heat, False)

                
                fan_rules = state["config"]["fan_rules"]
                ac_or_heat_on = ((state["heat"] == "on") or (state["ac"] == "on")) 
                for rule in fan_rules:
                    rule_state = rule["state"]
                    if ((rule_state == "on") and (state["fan"] != "on")):
                        log_info("Starting fan")
                        turn_fan(uart, client, True)

                    elif ((rule_state == "auto") and ((state["fan"] == "on") != ac_or_heat_on)):
                        # Auto fan needs to be on if any of heat or ac are on
                        # Note that in reality this code will only run to turn
                        # the fan off, the fan is turned on unconditionally for
                        # safety reasons at ac turn on time
                        log_info("Matching fan to ac/heat")
                        turn_fan(uart, client, ac_or_heat_on)

        mqtt_disconnect(client)

    finally:
        log_info("Exited main, writing configuration")
        # Write the configuration only at exit, don't want to wear out the flash
        # by writing to it on every target degree update
        # XXX This should write every day/hour if pending, otherwise when
        #     rebooting due to eg missing power it won't save
        write_config(config_filename, state)

if (__name__ == "__main__"):
    main()
        