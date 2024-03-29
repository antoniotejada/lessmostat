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


Script to deploy python files to a micropython device exposed via webrepl,
restart it, and display the console output forever

See https://forum.micropython.org/viewtopic.php?t=3124
See https://github.com/micropython/webrepl/blob/master/webrepl_cli.py
See https://github.com/Hermann-SW/webrepl/blob/master/webrepl_client.py
"""
import os
import struct
import sys
import time

# This is websocket-client-0.59.0
# See https://github.com/websocket-client/websocket-client.git
# See https://websocket-client.readthedocs.io/en/latest/
import websocket

import logging

import python_minifier as pymin

WEBREPL_REQ_S = "<2sBBQLH64s"
WEBREPL_PUT_FILE = 1
WEBREPL_GET_FILE = 2
WEBREPL_GET_VER  = 3

# Shorter versions of BINARY and TEXT opcode types
WS_BINARY_DATA = websocket.ABNF.OPCODE_BINARY
WS_TEXT_DATA = websocket.ABNF.OPCODE_TEXT

WS_DATA_TYPE_NAME = {
    WS_BINARY_DATA : "binary",
    WS_TEXT_DATA: "text" 
}

# WebSocket connection timeout, necessary because otherwise a recv would wait
# forever without responding to ctrl+c, and using ctrl+break would leave the
# webrepl in a bad state requiring to manually reboot the device. 1 is known to
# be too short for the timeout when receiving < 13KB file, longer timeouts will
# cause ctrl+c to have more latency when displaying the console forever
WS_TIMEOUT_SECS = 5

def send_req(ws, op, sz=0, fname=b""):
    rec = struct.pack(WEBREPL_REQ_S, b"WA", op, 0, 0, sz, len(fname), fname)
    logger.debug("sending %d: %r" % (len(rec), rec))
    ws.send(rec, WS_BINARY_DATA)
    
def recv_binary_data(ws):
    """
    Return the next binary data, ignore and skip any text data
    """
    while (True):
        op, data = ws.recv_data()

        logger.debug("op %d data %d %r" % (op, len(data), data))

        if (op == WS_BINARY_DATA):
            return data

def recv_ver(ws):
    send_req(ws, WEBREPL_GET_VER)
    data = recv_binary_data(ws)
    data = struct.unpack("<BBB", data)

    return data

def recv_file(ws, filename, out_filename):
    """
    Request and receive a file from the device
    """
    logger.info("recv_file %r %r", filename, out_filename)
    send_req(ws, WEBREPL_GET_FILE, fname=filename)

    data = recv_binary_data(ws)
    sig, code = struct.unpack("<2sH", data)
    if not ((sig == "WB") and (code == 0)):
        raise Exception("Unexpected sig %s or code %d", sig, code) 

    ws.send("\0", WS_BINARY_DATA)

    with open(out_filename, "wb") as f:
        while (True):
            data = recv_binary_data(ws)
            (sz,) = struct.unpack("<H", data[:2])

            if (sz != len(data) - 2):
                raise Exception("Data vs. size mismatch %d vs. %d", len(data)-2, sz)

            data = data[2:]
            f.write(data)
        
            if (sz == 0):
                logger.debug("completed get_file")
                break
                
            else:
                ws.send("\0", WS_BINARY_DATA)

    data = recv_binary_data(ws)
    sig, code = struct.unpack("<2sH", data)
    if not ((sig == "WB") and (code == 0)):
        raise Exception("Unexpected sig %s or code %d", sig, code)

def safe_recv_file(ws, url, password, filename, out_filename):
    # The remote will close and this will throw an exception if the log
    # file doesn't exist, trap and reconnect
    try:
        recv_file(ws, filename, out_filename)
    except websocket._exceptions.WebSocketConnectionClosedException as e:
        logger.exception("Unable to recv %s, re-connecting" % filename)
        logger.info("Connecting WebSocket")
        ws.connect(url,timeout=WS_TIMEOUT_SECS)
        send_login(ws, password)

def send_file(ws, filepath, out_filename=None):
    """
    Send a file to the device
    """
    logger.info("send_file %r %r", filepath, out_filename)
    if (out_filename is None):
        out_filename = os.path.basename(filepath)

    sz = os.path.getsize(filepath)
    send_req(ws, WEBREPL_PUT_FILE, sz, out_filename)
    
    data = recv_binary_data(ws)
    sig, code = struct.unpack("<2sH", data)
    if not ((sig == "WB") and (code == 0)):
        raise Exception("Unexpected sig %s or code %d", sig, code) 

    with open(filepath, "rb") as f:
        while True:
            # Sending 1024 chunks is known to make micropython timeout
            # sometimes, use 512
            buf = f.read(512)
            if (buf == ""):
                break
            logger.debug("Sending %d %r" %(len(buf), buf))
            ws.send(buf, WS_BINARY_DATA)
    
    data = recv_binary_data(ws)
    sig, code = struct.unpack("<2sH", data)
    if not ((sig == "WB") and (code == 0)):
        raise Exception("Unexpected sig %s or code %d", sig, code) 

def send_login(ws, password):
    """
    Wait for login prompt and send password
    """
    logger.info("Waiting for login prompt")
    while (True):
        prompt = ws.recv()
        if (prompt == "Password: "):
            break
        logger.info("Ignoring %r while waiting for login prompt" % prompt)
    logger.info("Sending password")
    ws.send("%s\r" % password)

def setup_logger(logger):
    logging_format = "%(asctime).23s %(levelname)s:%(filename)s(%(lineno)d):[%(thread)d] %(funcName)s: %(message)s"

    logger_handler = logging.StreamHandler()
    logger_handler.setFormatter(logging.Formatter(logging_format))
    logger.addHandler(logger_handler) 

    return logger

modules = ["config.py", "lessmostat.py", "logging.py", "main.py", "mqtt.py", "syncedtime.py", "umqtt_simple.py"]
log_filename = "lessmostat.log"
log_filepath = os.path.join("_out", log_filename)
cfg_filename = "lessmostat.cfg"
cfg_filepath = os.path.join("upython", cfg_filename)

logger = logging.getLogger(__name__)
setup_logger(logger)
log_level = logging.DEBUG
log_level = logging.INFO
logger.setLevel(log_level)

websocket.enableTrace(log_level == logging.DEBUG)
# XXX This doesn't seem to do anything, the important timeout is the one passed
#     in with the connection
websocket.setdefaulttimeout(5)

# host_password.txt a two line file with the host name in the first line and the
# password in the second
with open(os.path.join("_out", "host_password.txt"), "r") as f:
    host, password = [l.strip() for l in f.readlines()]
    url = "ws://%s:8266/" % host

logger.info("read host and password for host %s", host)

deploy = "deploy" in sys.argv[1:]
forever = "forever" in sys.argv[1:]

if (not (deploy or forever)):
    print "One of deploy or forever must be passed as argument!"
    raise Exception("Missing parameter") 

logger.info("Deleting log file %s", log_filepath)
try:
    os.remove(log_filepath)
except:
    logger.info("Can't remove log file %s", log_filepath)

if (deploy):
    ws = websocket.WebSocket()
    logger.info("Connecting WebSocket")
    ws.connect(url,timeout=WS_TIMEOUT_SECS)

    try:
        send_login(ws, password)

        ver = recv_ver(ws)
        logger.info("version %d.%d.%d" % ver)

        if (len(sys.argv) > 2):
            modules = sys.argv[2:]

        mini_size = 0
        maxi_size = 0
        
        for filename in modules:
            filepath = os.path.join("upython", filename)
            
            # It's not clear minifying helps with out of memory errors, but at
            # the very least fits more files on disk

            # The minifier is Python 2.x but the micropyton files are Python
            # 3.x, this causes problems in these two files, where some Python 3
            # code is converted to Python 2.x (print statement, bytes vs. str),
            # so don't minify them
            minify = filename not in ["logging.py", "umqtt_simple.py"]

            if (filepath.endswith(".py") and minify):
                try:
                    min_filepath = os.path.join("_out", filename)
                    logger.info("Minifying %s to %s", filepath, min_filepath)
                    with open(filepath, "r") as f:
                        s = f.read()
                    s_min = pymin.minify(s, filename=filename, remove_literal_statements=True)
                    with open(min_filepath, "w") as f:
                        f.write(s_min)

                    mini_size += len(s_min)
                    maxi_size += len(s)
                    logger.info("%d to %d %2.3f%%, total %d to %d %2.3f%%",  
                        len(s), len(s_min), len(s) * 100.0 / len(s_min),
                        maxi_size, mini_size, maxi_size * 100.0 / mini_size
                    )

                    filepath = min_filepath
                except:
                    logger.exception("Unable to minify %s", filepath)

            send_file(ws, filepath)

        safe_recv_file(ws, url, password, log_filename, log_filepath)
        
        # Break in by sending Ctrl+C
        logger.info("Sending ctrl+c")
        ws.send("\x03")

        # The config file is saved at ctrl+c time, send it now so it doesn't get
        # ovewritten
        # Wait some before sending it otherwise it races with the exception
        # handler that writes the file
        time.sleep(1)
        send_file(ws, cfg_filepath)

        # Restart by sending Ctrl+D
        # XXX Note this is a soft reset, different from machine.reset, which is
        #     a hard reset, should this do a hard reset?
        #     See https://docs.micropython.org/en/v1.8.6/wipy/wipy/tutorial/reset.html
        logger.info("Sending ctrl+d")
        ws.send("\x04")

    finally:
        ws.close()

if (forever):
    # Connect and display recv() forever, press ctrl+c to exit (takes a few seconds)
    # Note pressing ctrl+break will cause the interpreter to not exit cleanly,
    # ws.close() won't execute and will leave the webrepl session hanging forever,
    # the only solution is to reboot the micropython device

    ws = websocket.WebSocket()
    logger.info("Connecting WebSocket")
    ws.connect(url, timeout=WS_TIMEOUT_SECS)

    try:
        send_login(ws, password)

        ver = recv_ver(ws)
        logger.info("version %d.%d.%d" % ver)

        # Pulling the file here would hide micropython interpreter error
        # messages from the deploy above, only pull it if there was no deploy
        if (not deploy):
            safe_recv_file(ws, url, password, cfg_filename, cfg_filepath)
            safe_recv_file(ws, url, password, log_filename, log_filepath)

        try:
            with open(log_filepath, "r") as f:
                for l in f:
                    logger.info(l.strip())
        except:
            logger.info("Not dumping non-existent %s", log_filepath)

        # XXX Note that if the PC goes to sleep holding this connection it seems
        #     to hang micropython's network stack (!) and you won't be able to
        #     connect again via webrepl or have the device do any other network
        #     (mqtt, etc). Make main.py use a watchdog that detects when local
        #     network is not available and hard reset? See
        #     https://github.com/micropython/webrepl/issues/36
        logger.info("Displaying console forever, press ctrl+c to end")
        line = ""
        while (True):
            try:
                l = ws.recv()
                # The console sends CRLF on a different line, ignore those
                # XXX Accumulate and split by CRLF instead?
                while (True):
                    i = l.find("\r\n")
                    if (i == -1):
                        line += l
                        break
                    line += l[0:i]
                    logger.info(line)
                    line = ""
                    l = l[i+2:]

            except websocket.WebSocketTimeoutException:
                pass

    finally:
        logger.info("Closing WebSocket")
        ws.close()