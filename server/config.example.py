"""Copy this file to config.py. config.py is not committed.

GATEWAY is not contacted. A UDP connect to it only asks the operating system
which local address would be used for that route, so the server can print
the URL the ESP32 should post to. 8.8.8.8 works on a normal home network.
"""

GATEWAY = "8.8.8.8"
