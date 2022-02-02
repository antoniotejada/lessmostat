#!/usr/bin/env python
import lessmostat
import machine
import time
import sys

try:
    lessmostat.main()

except Exception as e:
    try:
        (year, month, mday, hour, minute, second, weekday, yearday) = time.localtime()
        with open("lessmostat.log", "a") as f:
            f.write("%d-%02d-%02d %02d:%02d:%02d Exception %s " % (
                year, month, mday,
                hour, minute, second,
                repr(e)
            ))
            sys.print_exception(e, f)
            
    except Exception as e:
        print("Exception writing exception to file: %s" % repr(e))

    # XXX This error handling is pretty heavy-handed and the wrong thing to do,
    #     since after reset it assumes the relays are closed, which doesn't seem
    #     to be the case for machine.reset (although it is for a power unplug reset)
    #     It should handle internet and mqtt errors as soft errors, and retry
    #     them, while keeping the thermostat function running.
    #     Other errors (out of memory?) could be handled the hard way
    #     Probably this reset doesn't even reconnect to the wifi anyway?
    time.sleep(20)
    machine.reset()
