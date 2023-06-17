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
import errno
import time

import ntptime

from logging import log_info

# epoch is year 2000 in MicroPython but 1970 in unix
# See https://stackoverflow.com/questions/57154794/micropython-and-epoch

uepoch_delta_seconds = 946_684_800
def get_epoch():
    return time.time() + uepoch_delta_seconds


# The esp8266 RTC drifts a lot (seconds per minute), may depend on temperature
# so periods with relays on may have higher drifts than periods without relays
# on
# See https://github.com/micropython/micropython/issues/2724 
# See https://docs.micropython.org/en/latest/esp8266/general.html#real-time-clock

# Sync with NTP every this many seconds
# With 240 seconds of sync time, max observed drift in the report below is -7
# (and was observed when the relays had been on for some time)
min_ntp_sync_time = 240
g_last_ntp_sync_time = 0

def sync_time_with_ntp():
    global g_last_ntp_sync_time
    synced = False
    
    # Correct esp2866 clock drift (several seconds per minute) by doing
    # NTP sync
    # XXX This may result in non-monotonic timestamps depending on the 
    #     drift direction, do we care?
    if ((get_epoch() - g_last_ntp_sync_time) < min_ntp_sync_time):
        return synced

    # Setup the clock using ntp
    # Note ntptime.settime() is known to timeout, try a few times
    ntp_retries = 5
    while (ntp_retries > 0):
        try:
            log_info("Querying NTP server")
            ntp_retries -= 1
            before = get_epoch()
            ntptime.settime()
            after = get_epoch()
            synced = True
            # Note this drift can be ~1 second misreported either way since
            # includes the time it takes to settime (which is less than one 
            # second for the NTP query, since it has a 1 second timeout, plus 
            # whatever time for the other calculations)
            log_info("Got NTP, drift was around %d" % (after - before))
            break

        except OSError as e:
            # Note Micropython will return -ENOENT (OSError -2) instead of
            # ENOENT (2) if the NTP server can't be found, normally because of
            # network down
            if (e.errno not in [errno.ETIMEDOUT, -errno.ENOENT]):
                log_info("Unexpected NTP error %d" % e.errno)
                raise

            else:
                log_info("Timeout querying NTP, retries left %d, sleeping" % ntp_retries)
                time.sleep(1)
        
        # Note this may not have sync'ed if sync_time_with_ntp hit a timeout or
        # network down. That's ok since we still want to wait some time before
        # trying to sync again (and possibly timeout again)
        g_last_ntp_sync_time = get_epoch()
    
    return synced