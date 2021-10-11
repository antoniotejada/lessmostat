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
import ntptime
import os
from umqtt_simple import MQTTClient
import ujson as json
import ubinascii as binascii
import time

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
def read_config(state):
    print("Reading config file", config_filename)
    try:
        with open(config_filename, "r") as f:
            js = f.read()
            print("Read config data", js)
            state["config"].update(json.loads(js))

    except Exception as e:
        print("Exception reading config file %s" % config_filename, e)

    
def write_config(state):
    print("Writing config file", config_filename)
    try:
        with open(config_filename, "w") as f:
            js = json.dumps(state["config"])
            f.write(js)
            print("Written config data", js)

    except Exception as e:
        print("Exception writing config file %s" % config_filename, e)

# From https://github.com/micropython/micropython-lib/blob/master/python-stdlib/functools/functools.py
def partial(func, *args, **kwargs):
    def _partial(*more_args, **more_kwargs):
        kw = kwargs.copy()
        kw.update(more_kwargs)
        return func(*(args + more_args), **kw)

    return _partial

def sub_cb(client, topic, msg):
    """

    Start at the given temp or higher
    { "state" : "on", temp="26" }

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
    print("callback for topic %s msg %s" % (repr(topic), repr(msg)))

    try:
        d = json.loads(msg)

        if (topic.endswith("/state")):
            publish_message(client, "info/state", { 'state' : state } )

        elif (topic.endswith("control/ac")):
            state["config"]["ac_rules"] = [
                { "state" : "on", "temp" : d["temp"] },
            ]
            # XXX Should this publish only the delta?
            publish_message(client, "info/state", { 'state' : state } )
            
    except Exception as e:
        print("Exception handling topic %s message %s" % (repr(topic), repr(msg)), e) 


# epoch is year 2000 in MicroPython but 1970 in unix
# See https://stackoverflow.com/questions/57154794/micropython-and-epoch
uepoch_delta_seconds = 946_684_800
def get_epoch():
    return time.time() + uepoch_delta_seconds

def timestamp_message(msg):
    msg["ts"] = get_epoch()
    return msg

topic_root = "apartment/lessmostat/"
def publish_message(client, subtopic, msg):
    js = json.dumps(timestamp_message(msg))
    client.publish(bytes(topic_root + subtopic, 'UTF-8'), bytes(js, 'UTF-8'))


# See http://www.icstation.com/esp8266-wifi-channel-relay-module-smart-home-remote-control-switch-android-phone-control-transmission-distance-100m-p-12592.html
relay_1_on = [0xA0, 0x01, 0x1, 0xA2]
relay_1_off = [0xA0, 0x01, 0x00, 0xA1]
relay_2_on = [0xA0, 0x02, 0x01, 0xA3]
relay_2_off = [0xA0, 0x02, 0x00, 0xA2]

def uart_write(uart, b):
    uart.write(b)
    # XXX Looks like some delay is needed between uart writes otherwise the 
    #     second write is lost and the relay state is not modified
    #     This is the case even with some code between uart_write invocations
    time.sleep(1)

fan_on = bytes(relay_2_on)
fan_off = bytes(relay_2_off)
def turn_fan(uart, client, on):
    if (on):
        # XXX Should prevent somewhere it's not trying to re-enable fan before the
        #     safety idle period
        uart_write(uart, fan_on)
        fan_state = "on"

    else:
        uart_write(uart, fan_off)
        fan_state = "off"

    publish_message(client, "info/fan", {'state' : fan_state})
    state["fan"] = fan_state
    state["fan_mod_ts"] = get_epoch()

ac_on = bytes(relay_1_on)
ac_off = bytes(relay_1_off)
def turn_ac(uart, client, on):
    if (on):
        # Always turn fan on before ac
        # XXX Not necessary if fan is on by default?
        turn_fan(uart, client, on)

        # XXX Should prevent somewhere it's not trying to re-enable ac before the 
        #     safety idle period

        uart_write(uart, ac_on)
        ac_state = "on"
        
    else:
        uart_write(uart, ac_off)
        ac_state = "off"
        
        # Always turn fan off after ac
        # XXX Not necessary if fan is on by default?
        turn_fan(uart, client, on)

    publish_message(client, "info/ac", { 'state' : ac_state })
    state["ac"] = ac_state
    state["ac_mod_ts"] = get_epoch()

state = { 
    # Current state
    "ac" : None,
    "fan" : None,
    "sensor" : { "humid" : None, "temp" : None } ,

    # Configuration state
    # XXX This may want to save the mod_ts so it doesn't continuously 
    #     restart the AC. Alternatively always wait the safety period when 
    #     restarting the program
    "config" : {
        "mqtt_broker" : "192.168.8.200",
        "ac_rules" : [
            { "state" : "on", "temp" : 30 },
        ],
        "fan_rules" : [],
        # On and off thresholds in 1/10th of a celsius degree (ie will turn on
        # at temp + on threshold and off at temp - off threshold)
        "on_threshold_decidegs" : 4, 
        "off_threshold_decidegs" : 4,
    }
}

def main():
    try:
        print("Reading initial configuration")
        read_config(state)
        print("Initial state is", state)

        # Fetch some fixed values from the config
        on_threshold_decidegs = state["config"]["on_threshold_decidegs"]
        off_threshold_decidegs = state["config"]["off_threshold_decidegs"]
        mqtt_broker = state["config"]["mqtt_broker"]

        # Setup the clock using ntp
        # Note ntptime.settime() is known to timeout, try a few times
        ntp_retries = 5
        while (ntp_retries > 0):
            try:
                print("Querying NTP server")
                ntp_retries -= 1
                ntptime.settime()
                print("Got NTP")
                break
            except OSError as e:
                if (e.errno != errno.ETIMEDOUT):
                    raise
                else:
                    print("Timeout querying NTP, retries left %d, sleeping" % ntp_retries)
                    time.sleep(1)

        client_id = binascii.hexlify(machine.unique_id())

        print("Initializing relays uart")
        uart = machine.UART(0, baudrate=115200, bits=8, parity=None, stop=1)
        # Detach REPL from UART so UART can be used for the relays
        print("Detaching UART from repl")
        os.dupterm(uart, 1)

        # The DHT is connected to the 5V, GND and RX (gpio 3)
        # Steal the RX pin from the uart, see
        # https://forum.micropython.org/viewtopic.php?t=6669
        print("Initializing DHT sensor")
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
                    print("DHT sensor timed out, retrying")

                else:
                    raise

        print("Connecting %s with MQTT broker %s" % (client_id, mqtt_broker))
        client = MQTTClient(client_id,  mqtt_broker)
        client.set_callback(partial(sub_cb, client))
        client.connect()

        # Now that we have a client and a uart, turn ac and fan off to start and
        # advertise in a known state
        turn_ac(uart, client, False)
        turn_fan(uart, client, False)
        
        # Now that the state is known, accept control commands
        client.subscribe(bytes(topic_root + "control/+", 'UTF-9'))

        gc.collect()
        
        while (True):
            # Gather sensor information
            # Do sparingly since this can take 2s on DHT22, 1s on DHT11
            dht_sensor.measure()

            temp = dht_sensor.temperature()
            humid = dht_sensor.humidity()

            # Publish sensor information
            # XXX Should this only publish changes?
            publish_message(client, "info/sensor", {'temp' : temp, 'humid' : humid})

            state["sensor"]["temp"] = temp
            state["sensor"]["humid"] = humid

            # Wait some seconds between reporting sensor data (the wait could be
            # longer depending on the execution time of the loop below, but it's
            # okay even if we ever need some time accurate policy because sensor
            # messages contain timestamp)
            sleep_ms = 10_000
            # Sleep a few millis between message checks so target temperature
            # updates on the client are responsive
            sleep_iteration_ms = 500
            iterations = sleep_ms / sleep_iteration_ms
            for i in range(iterations):
                # Non-blocking check for messages
                client.check_msg()
                time.sleep_ms(sleep_iteration_ms)

                # Check rules
                # Collect active rules
                # No need to refresh temp, will be refreshed in the next 30s
                # XXX This only needs checking if the rules changed, since the
                #     temperature is only updated above
                ac_rules = state["config"]["ac_rules"]
                for rule in ac_rules:
                    rule_state = rule["state"]
                    rule_temp = rule.get("temp", None)
                    if (rule_temp is not None):
                        if ((state["ac"] != "on") and (rule_state == "on") and 
                            (temp*10 >= rule_temp*10 + on_threshold_decidegs)):
                            print("Starting fan and ac")
                            turn_ac(uart, client, True)
                            
                        elif ((state["ac"] != "off") and (rule_state == "on") and 
                            (temp*10 <= rule_temp*10 - off_threshold_decidegs)):
                            print("Stopping fan and ac")
                            turn_ac(uart, client, False)

        client.disconnect()

    finally:
        print("Exited main, writing configuration")
        # Write the configuration only at exit, don't want to wear out the flash
        # by writing to it on every target degree update
        write_config(state)


if __name__ == "__main__":
    main()
        