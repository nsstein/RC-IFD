# Architecture

This document explains how RC-IFD is put together. For operating instructions, see the [User Guide](USER_GUIDE.md).

## Components

| Path | Responsibility |
| --- | --- |
| `rc_ifd.py` | Entry point. Parses CLI options, starts the backend on a worker thread with its own asyncio loop, opens the display window, and handles single-instance reuse. |
| `ifd_backend/server.py` | Local HTTP server and hand-written WebSocket endpoint. Serves `web/index.html`, exposes `/api/info` and `/logs/*`, manages link state, settings, clients and broadcast events. |
| `ifd_backend/links.py` | Serial, UDP and TCP transports. Normalises received bytes into `on_rx(data)` and state changes into `on_state(state, text)`. |
| `ifd_backend/recorder.py` | Writes `.ifdrec` raw logs, MAVLink `.tlog` files and JSON sidecars directly from the link. Handles idle splitting, pruning, recovery and disk guards. |
| `ifd_backend/adsb.py` | Polls online aggregators and local receivers, normalises targets to SI units, merges by ICAO address, and applies rate-limit/backoff rules. |
| `ifd_backend/window.py` | Chooses pywebview, Chromium/Edge/Chrome app mode, or the default browser. Pi prefers GPU-accelerated Chromium kiosk. |
| `web/index.html` | The complete display: protocol parsers, instrument rendering, alerts, missions, traffic, replay UI and Settings drawer. No build step and no external JavaScript dependencies. |
| `tools/` and `tests/` | Telemetry simulator and offline test suites. |
| `deploy/` | Linux udev rule and Raspberry Pi service/kiosk installer. |

## Data flow

```text
vehicle ──bytes──▶ Link ──bytes──▶ IFDServer ──WebSocket binary──▶ display parsers ──▶ canvas instruments
                     │                 │
                     │                 ├──▶ Recorder ──▶ logs/ (raw, tlog, JSON)
                     │                 └──▶ JSON control/events ──▶ display status, settings, log list
                     └────────────────◀── MAVLink requests / MSP polls from the controller client
```

All received bytes are recorded **before** they reach the UI. A frozen or closed display therefore cannot create a gap in the log. Binary WebSocket frames carry telemetry in both directions; text frames carry JSON control messages (`hello`, `ports`, `open`, `close`, `rec`, `logs`, `settings_save`, `adsb_cfg`, `adsb_pos`, and so on).

### Controller and viewers

The first local WebSocket client becomes the *controller*: only its outbound bytes are written to the link. Additional clients are read-only viewers. This prevents a phone from changing the connection or duplicating MSP polls. When a local controller leaves, the server promotes another local client if one exists.

The HTTP server binds to `127.0.0.1` by default. `--lan` binds to all interfaces so phones and tablets can view the display, but viewers remain read-only. There is no authentication: use `--lan` only on a trusted local network, never on the public internet.

## Process and window modes

`rc_ifd.py` starts the asyncio server in a daemon thread so the window can be closed without killing the backend. The main thread then chooses a window mode:

- **`auto`** — pywebview on desktop; Chromium app/kiosk on Raspberry Pi; default browser as fallback.
- **`webview`** — native window via pywebview (`WindowApi` exposes native full-screen toggling).
- **`chromium`** — Chrome/Edge/Chromium with `--app`, its own browser profile, and optional `--kiosk`.
- **`browser`** / **`none`** — default browser, or backend-only (`--headless`).

A second launch detects an already-running instance through `/api/info` and opens another window onto it instead of starting a competing link.

Opening `web/index.html` as a file bypasses the backend. In that mode the display uses Web Serial, a WebSocket bridge, the simulator or a local replay file; logging and ADS-B feeds are not available.

## Recorder design

| Concern | Approach |
| --- | --- |
| Format fidelity | `.ifdrec` stores `[u64 µs timestamp][u32 length][bytes]` records with an `IFDREC1 <proto>` header so any protocol can be replayed exactly. |
| MAVLink compatibility | `MavFramer` validates CRC_EXTRA where known and resynchronises on STX otherwise; whole frames are written as standard `.tlog`. |
| Power loss | Buffers flush every second and `fsync` every five seconds. Logs left `state: open` are marked `recovered` at startup. |
| Noise control | Auto logs under 2 KiB or 3 seconds are discarded unless manually requested. |
| Capacity | New logs prune the oldest files above `--max-log-mb` or below `--min-free-mb`; recording stops if free space becomes critically low. |
| Events | The UI reports arm/disarm, mode changes and warnings; they are capped and stored in the JSON sidecar. |

## ADS-B pipeline

`AdsbFeed` accepts a configuration with three groups, any of which can run simultaneously:

1. **Built-in online services** — `adsb.lol` and `adsb.fi`, queried around a moving point.
2. **Custom online services** — up to eight ADSBExchange-v2-compatible URL templates with optional API key and header name. Placeholders include `{lat}`, `{lon}`, `{dist}` (nautical miles), `{dist_nm}`, `{dist_km}`, `{dist_m}` and `{key}`.
3. **Local receiver** — `aircraft.json` (readsb/dump1090/tar1090/PiAware) or an SBS-1 `host:30003` stream.

The query centre is the live aircraft position, falling back to last-known position and then home; it can also be fixed or home-based. Targets are de-duplicated by ICAO address, with the fresher position winning. HTTP 429 and 401/403 responses trigger increasing backoff so a rate-limited or blocked service is not hammered. `ADSB_VEHICLE` messages from MAVLink are accepted directly by the display and are not stored in flight logs.

## Settings and persistence

Settings originate in the display's `localStorage` and, when the backend is present, are mirrored to `settings.json` in the data directory via `settings_save`. The backend restores `autoLog` and ADS-B configuration at startup. The last-known position is kept in browser storage and refreshed every two seconds; it is per browser profile, so a fresh profile starts without it.

Default data locations:

| Platform | Path |
| --- | --- |
| Windows | `%LOCALAPPDATA%\RC-IFD` |
| macOS | `~/Library/Application Support/RC-IFD` |
| Linux / Pi | `~/.local/share/rc-ifd` (or `$XDG_DATA_HOME/rc-ifd`) |

## Extension points

- Add a transport in `ifd_backend/links.py` and a matching case in `IFDServer._open_link`.
- Add an ADS-B service by extending `BUILTIN` in `ifd_backend/adsb.py`; add a parser in `parse_aircraft_json` for a new payload shape.
- Add an instrument by extending the canvas rendering functions in `web/index.html` and feeding them from the existing telemetry state.
- Add an event to the log sidecar with `Backend.send({op: "event", ...})`.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for test commands and change guidelines.
