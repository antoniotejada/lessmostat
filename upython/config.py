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

import logging

def read_config(config_filename, state):
    logging.log_info("Reading config file %r" % config_filename)
    try:
        with open(config_filename, "r") as f:
            js = f.read()
            logging.log_info("Read config data %r" % js)
            state["config"].update(json.loads(js))

            # Validate the configuration
            if (len(state["config"]["fan_rules"]) == 0):
                # If no fan rules, assume auto
                state["config"]["fan_rules"] = [ { "state" : "auto" } ]

    except Exception as e:
        logging.log_exception("Exception reading config file %r" % config_filename, e)

def write_config(config_filename, state):
    logging.log_info("Writing config file %r" % config_filename)
    try:
        with open(config_filename, "w") as f:
            js = json.dumps(state["config"])
            f.write(js)
            logging.log_info("Written config data %r" % js)

    except Exception as e:
        logging.log_exception("Exception writing config file %r" % config_filename, e)

