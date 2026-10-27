# Vermiculture sensors

The ESP32 reads the temperature probes, serves the chart page, and keeps about 48 hours of readings on the board. It also posts each reading to a Python server on a PC, which appends the long-term log.

The chart in the browser loads history once, then requests the latest reading every minute. The board measures about every 15 minutes.

## First upload

### 1. Install the Arduino tools

Install the [Arduino IDE](https://www.arduino.cc/en/software) and the **esp32** board package by Espressif, version 3 or newer.

Install these libraries from Library Manager:

- **OneWire** by Paul Stoffregen
- **DallasTemperature** by Miles Burton

Open this repository as the sketch folder. The folder name and `vermiculture-sensors.ino` need to stay matched.

### 2. Fill in secrets

Copy `secrets-example.h` to `secrets.h`. `secrets.h` stays on this machine and is not committed.

Set the Wi-Fi name and password. Leave `SERVER_URL` until the PC server is running; step 4 prints the address to put there.

### 3. Select the board settings

In the Arduino IDE:

- **Board:** ESP32 Dev Module, or the specific ESP32 board you are using
- **Partition Scheme:** Default 4MB with spiffs (1.2MB APP/1.5MB SPIFFS)
- **Port:** the ESP32's serial port

The partition scheme matters. History is stored in the SPIFFS area with LittleFS. A scheme without that area serves the page, but readings disappear when the board restarts.

### 4. Start the PC server and finish secrets

From the `server` directory:

```
python serve.py
```

Python 3 is enough. There is nothing to install with pip.

The window prints two addresses. Open the localhost one to check the PC page. Copy the `ESP32 posts` address into `SERVER_URL` in `secrets.h`. It looks like `http://192.168.x.x:8080/readings`. Leave this window open while the board is posting.

Optional: copy `server/config.example.py` to `server/config.py` and set `GATEWAY` when `8.8.8.8` is not the right route for finding this PC's LAN address.

### 5. Upload

Upload the sketch. Open the Serial Monitor at **115200** baud.

When Wi-Fi connects, the monitor prints the chart address, for example `http://192.168.x.x/` and `http://vermiculture.local/`. Open that address on a phone or computer on the same Wi-Fi.

The board sets its clock from the internet (Pacific time, including daylight saving). It starts storing on-board history after the clock is set. Until then, posts still go to the PC, and the PC stamps the arrival time.

The first boot prints `Started a new reading ring.` Later boots print how many samples are stored.

## When a file changes

| File | What to do |
| --- | --- |
| `vermiculture-sensors.ino` | Upload the sketch again. |
| `sensor_server.cpp`, `sensor_server.h` | Upload the sketch again. |
| `ble_in_use.c` | Upload the sketch again. This file keeps Bluetooth memory reserved. |
| `secrets.h` | Upload the sketch again. |
| `secrets-example.h` | Nothing on the board until those values are copied into `secrets.h` and uploaded. |
| `web/index.html` | From the repository root run `python tools/pack_page.py`, then upload the sketch. The PC server reads this file on each page load, so refresh the browser; do not restart Python for a page-only change. |
| `web_page_gz.h` | Do not edit. `tools/pack_page.py` rewrites it from `web/index.html`. Upload the sketch after it is regenerated. |
| `tools/pack_page.py` | No upload. The next page pack uses the new script. |
| `server/serve.py` | Stop the Python server and start `python serve.py` again from the `server` directory. |
| `server/config.py` | Restart the Python server. |
| `server/config.example.py` | Nothing until it is copied to `server/config.py` and the Python server is restarted. |
| `server/readings.jsonl` | This is the PC archive. Leave it in place. Removing it starts the PC log over. The on-board ring is separate. |
| `README.md` | No upload and no server restart. |

Probe names live in `web/index.html` (`PROBE_NAMES`). Changing them means packing the page and uploading.

Changing the layout of `ReadingSample` in `sensor_server.h` makes the board discard its ring on the next boot and start a new one. The PC log in `readings.jsonl` is left as it is.

The on-board clock zone is `TIME_ZONE` in `sensor_server.cpp`. Changing it requires another upload.
