#!/usr/bin/env python
import machine
import time

# Sleep some before importing local modules so web repl can reconnect, log in,
# and see any compile errors 
# See https://forum.micropython.org/viewtopic.php?t=7457
time.sleep(5)

import lessmostat
from logging import log_set_filename, log_exception

log_set_filename("lessmostat.log")

# XXX Could this use multiprocess so the webrepl can be used at the same time?
try:
    lessmostat.main()

except Exception as e:
    log_exception("Exception running lessmostat.main", e)

    # Do some hysteresis sleep and a hard reset, exceptions arriving here are
    # supposed to be severe enough that they require hard reset
    time.sleep(20)
    machine.reset()
