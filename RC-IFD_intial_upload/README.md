# RC Instrument Flight Display — Desktop v2.0

Instrument display for flying an RC aircraft home when video fails. It runs on Windows, macOS, Linux and Raspberry Pi and reads MAVLink, CRSF or MSP telemetry.

## How it works

```
 Flight controller / radio                  Python backend (ifd_backend)                     Display (web/index.html)
 ───────────────────────────               ───────────────────────────────                  ─────────────────────────
 USB-serial / Pi GPIO UART   ──bytes──▶    SerialLink (pyserial, auto-reconnect)   ──WS──▶  same protocol parsers + instruments
 UDP (MAVProxy, Wi-Fi bridge)──bytes──▶    UdpLink / TcpLink                       ◀──WS──  MAVLink requests, MSP polls
 TCP (ESP32 :5760, SITL)                    Recorder → logs/*.ifdrec, *.tlog, *.json         log browser, replay with seek/pause
```

* **The backend owns the link and the log.** Closing, crashing or reloading the display does not drop the link or stop logging. When the display reopens it re-attaches to the running link without starting a new log.
* **The display is the tested v1.2 HTML, with additions for the desktop backend.** It is served locally and shown in a native window (pywebview) or a Chrome/Edge/Chromium app window. If you open the file directly, without the backend, it still works in browser mode.
* **Dependencies:** standard-library Python 3.9+, plus `pyserial`. `pywebview` is optional.

## Install and run

| OS | Steps |
|---|---|
| **Windows 10/11** | Install Python 3.9+ (tick *Add to PATH*). Double-click `run_windows.bat`. The first run creates `.venv` and installs `pyserial` and `pywebview`. |
| **macOS** | `./run_mac_linux.sh`. Serial ports appear as `/dev/cu.*` (the `/dev/tty.*` duplicates are hidden). |
| **Linux desktop** | `./run_mac_linux.sh`. Once: `sudo usermod -aG dialout $USER`, then copy `deploy/linux/99-rc-ifd.rules` to `/etc/udev/rules.d/`. |
| **Raspberry Pi 4/5** | `bash deploy/raspberrypi/install_pi.sh` — see below. |

Options: `--fullscreen`, `--kiosk`, `--headless`, `--lan`, `--keep-running`, `--window auto|webview|chromium|browser|none`, `--data-dir`, `--max-log-mb`, `--min-free-mb`.

## Connecting (Settings ⚙ → Connection)

| Transport | Use it for | Notes |
|---|---|---|
| **Serial port** | FC on USB, USB-UART on a telemetry port, CRSF tap, Pi `/dev/serial0` | Ports are listed with their USB name and VID:PID. Common flight controllers and adapters are recognised. Baud rates include 416666 and 420000 for CRSF. **Hold DTR/RTS low** stops ESP32/Arduino/CP210x devices resetting when the port opens. |
| **UDP** | MAVProxy `--out`, mavlink-router, ELRS/ESP Wi-Fi backpacks, SITL | Listens on a port; replies go to the last sender. |
| **TCP client** | ESP32 Wi-Fi bridges (`192.168.4.1:5760`), SITL `:5760` | Reconnects automatically, waiting a little longer between each attempt. |
| **Simulator** | Bench / UI testing | Same as v1.2. |
| **Replay** | Logged flights | See *Flight logs*. |

**How the serial connection handles problems in the field**

* **Unplug or brown-out:** the backend reports *lost*, then re-opens the same adapter. It matches by path first, then by USB VID:PID and serial number, because Windows may assign a new COM number and Linux/macOS may rename the device on replug.
* **Port already in use:** ports are opened for exclusive use on Linux/macOS, so a configurator or GCS can't silently share the port. If another program has it, you get a clear "in use / busy" message.
* **Clear error messages:** permission problems (Linux `dialout` group), a missing driver (CH340/CP210x), or a baud rate the adapter doesn't support each produce a message saying what to do.
* **ModemManager:** on Linux it probes newly plugged USB serial devices and corrupts the start of the MAVLink/MSP stream. The udev rule stops it.

**Extra displays:** start with `--lan` and open `http://<pi-ip>:8765/` on a phone or tablet. Only the first local window controls the link. Other screens are read-only, so MSP is never polled twice and a viewer cannot change the link.

## Flight logs

* **Auto-log (on by default):** a new log starts when telemetry arrives. It splits after 60 s with no data, and connect/disconnect sessions shorter than 3 s are discarded. **● Log now** records manually.
* **Files** (in `logs/` inside the data folder, which **Open log folder** opens):
  * `*.ifdrec` — exact bytes with µs timestamps, for all protocols; used for replay.
  * `*.tlog` — MAVLink only; standard format that opens in Mission Planner, MAVExplorer, QGroundControl and pymavlink.
  * `*.json` — summary: protocol, link, start/end, arm/disarm, mode changes and warnings raised during the flight.
* **Power-loss safety:** data is flushed every 1 s and synced to disk every 5 s, so pulling the battery loses at most ~5 s. Logs that weren't closed properly are marked ⚠ *recovered* and still replay.
* **Disk limits:** the oldest logs are deleted when the total passes `--max-log-mb` (2000) or free space drops below `--min-free-mb` (300).
* **Replay:** press ▶ on any log. The bar at the bottom gives play/pause (Space), a seek slider, ½–10× speed and **✕ Live**. Seeking rebuilds the exact aircraft state from the log, including home, flight modes and the breadcrumb trail. While paused, the instruments hold their values instead of going stale.

| Data folder | Path |
|---|---|
| Windows | `%LOCALAPPDATA%\RC-IFD` |
| macOS | `~/Library/Application Support/RC-IFD` |
| Linux / Pi | `~/.local/share/rc-ifd` |

Settings are also stored there (`settings.json`), so kiosk and fresh browser profiles keep them.

## Raspberry Pi

`install_pi.sh` sets up the following:

* **Packages:** `python3-serial` and Chromium.
* **Serial access:** the `dialout` group, plus the udev rule that keeps ModemManager away.
* **Backend:** a **systemd user service** (`rc-ifd.service`) starts at boot without needing a screen, so logging works with no display attached.
* **Display:** full-screen **Chromium kiosk** at login. Chromium is GPU-accelerated on the Pi, which makes it faster than WebKitGTK for this canvas UI.
* **Frame rate:** on a Pi the display defaults to a **30 fps cap** (Settings → Display) to save power and heat.
* **GPIO UART:** to use the Pi's own UART (GPIO14/15), disable the serial console in `raspi-config` and choose `/dev/serial0`.

Recommended hardware: Pi 4 (2 GB+) or Pi 5, with a 7–10" HDMI/DSI screen bright enough to read outdoors. Use a good SD card or USB SSD. Power it from a 5 V/3 A+ supply that can't brown out when the radio transmits.

## Tests

```
python tests/test_backend.py      # framer, recorder, server, UDP link, serial unplug/re-enumeration (fake pyserial on PTYs)
node   tests/ifd_engine_tests.js  # MAVLink/CRSF/MSP codecs + corruption resync
python tools/telemetry_sim.py --udp 127.0.0.1:14550   # stand-in MAVLink aircraft for bench tests (--pty for a virtual serial port)
```

## Single-file executable (optional)

`pip install pyinstaller` then `python build_standalone.py`. This builds `dist/RC-IFD/` for the OS you build it on.
