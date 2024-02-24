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
import os
import sys
import time

g_log_filename = "logging.log"
g_max_log_size_bytes = 16 * 1024
g_log_file = None

def log_set_max_file_size(max_size_bytes):
    global g_max_log_size_bytes
    g_max_log_size_bytes = max_size_bytes

def log_set_filename(filename):
    global g_log_filename
    if (g_log_file is not None):
        raise Exception("Can't change the filename to %r with the log file open" % g_log_filename)

    g_log_filename = filename

def log_exception(msg, e, stdout_only = False):
    log_all(msg, e, stdout_only)

def log_info(msg, stdout_only = False):
    log_all(msg, None, stdout_only)

def log_all(msg, e=None, stdout_only = False):
    # XXX This should merge log entries, but also have a max log lines per hour
    #     (eg what about ping pong of exceptions eg when network is down)
    global g_log_file

    (year, month, mday, hour, minute, second, weekday, yearday) = time.localtime()
    s = "%d-%02d-%02d %02d:%02d:%02d " % (
        year, month, mday,
        hour, minute, second
    )

    # Do individual prints instead of string interpolation to prevent memory
    # errors because of interpolating long strings
    print(s, msg)
    
    if (e is not None):
        print(e)
        sys.print_exception(e, sys.stdout)

    if (not stdout_only):
        try:
            mode = "a+"
            try:
                st = os.stat(g_log_filename)
                if (st[6] > g_max_log_size_bytes):
                    log_info("Resetting %r over %d" % (g_log_filename, g_max_log_size_bytes), True)
                    mode = "w"
                    if (g_log_file is not None):
                        g_log_file.close()
                        g_log_file = None
                    
            except Exception as e:
                # Note this can happen if the file doesn't exist, so open the
                # file anyway
                log_exception("Unable to stat %r" % g_log_filename, e, True)

            if (g_log_file is None):
                g_log_file = open(g_log_filename, mode)

            g_log_file.write(s)
            g_log_file.write(msg)
            g_log_file.write("\n")
            if (e is not None):
                sys.print_exception(e, g_log_file)
            g_log_file.flush()

        except Exception as e:
            log_exception("Exception writing exception to file", e, True)
