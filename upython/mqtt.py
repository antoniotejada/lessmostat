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
"""
import ujson as json

from umqtt_simple import MQTTClient

from logging import log_info, log_exception
from syncedtime import get_epoch

# XXX This should be in some utils file?
def str_to_bytes(s):
    return bytes(s, 'UTF-8')

# XXX This should be in some utils file?
# From https://github.com/micropython/micropython-lib/blob/master/python-stdlib/functools/functools.py
def partial(func, *args, **kwargs):
    def _partial(*more_args, **more_kwargs):
        kw = kwargs.copy()
        kw.update(more_kwargs)
        return func(*(args + more_args), **kw)

    return _partial

def timestamp_message(msg):
    msg["ts"] = get_epoch()
    return msg

def mqtt_create(mqtt_broker, client_id, topic_root, callback):
    log_info("Creating MQTT client id %s for broker %s" % (client_id, mqtt_broker))

    client = MQTTClient(client_id,  mqtt_broker)

    d = { 
        "topic_root" : topic_root, 
        "broker" : mqtt_broker,
        "id" : client_id,
        "client" : client
    }     

    client.set_callback(partial(callback, d))
    
    return d

def mqtt_connect(client):
    log_info("Connecting %s with MQTT broker %s" % (client["id"], client["broker"]))
    try:
        # This raises EHOSTUNREACH (errno 113), ECONNABORTED (errno 103)
        client["client"].connect()

        client["client"].subscribe(str_to_bytes(client["topic_root"] + "control/+"))
    except Exception as e:
        log_exception("Exception connecting to MQTT", e)

def mqtt_publish_message(client, subtopic, msg):
    js = json.dumps(timestamp_message(msg))
    try:
        # This raises OSERROR (-1), ECONNRESET (errno 114), ECONNABORTED (errno
        # 113) on error
        client["client"].publish(str_to_bytes(client["topic_root"] + subtopic), str_to_bytes(js))
    
    except OSError as e:
        # Ignore connection errors when publishing messages, let check_msg in
        # the main loop retry the connection and subscribe
        # Log only to stdout, as this can be noisy
        log_exception("Exception publishing message", e, True)

def mqtt_publish_state_message(client, state):
    mqtt_publish_message(client, "info/state", { 'state' : state } )

def mqtt_check_msg(client):
    return client["client"].check_msg()

def mqtt_disconnect(client):
    client["client"].disconnect()