# Contributing

Thanks for helping improve RC-IFD. This project is a pilot aid, so correctness and predictable failure behaviour matter more than feature count.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

On Windows use `.venv\Scripts\python`. You do not need pywebview to run the tests; it is only used for the native window.

Run the app from the project root:

```bash
python rc_ifd.py                 # desktop backend + display
python rc_ifd.py --window browser
python tools/telemetry_sim.py --udp 127.0.0.1:14550
```

Then select **UDP** in Settings → Connection and use port `14550`.

## Tests

Run all three suites before opening a change:

```bash
python tests/test_backend.py      # recorder, server, links, WebSocket control channel
python tests/test_adsb.py         # feed parsing, merging, backoff and Test button
node   tests/ifd_engine_tests.js  # MAVLink / CRSF / MSP codecs and resync
```

The serial tests use a fake pyserial backed by pseudo-terminals on POSIX; that portion is skipped on Windows. Tests must not require hardware or network access.

## Code map

| Area | Where to start |
| --- | --- |
| Launcher and CLI | `rc_ifd.py` |
| HTTP / WebSocket server | `ifd_backend/server.py` |
| Telemetry transports | `ifd_backend/links.py` |
| Flight logs | `ifd_backend/recorder.py` |
| ADS-B feeds | `ifd_backend/adsb.py` |
| Window selection | `ifd_backend/window.py` |
| Display UI and protocols | `web/index.html` (single file, no build step) |
| Pi / Linux deployment | `deploy/` |

See [Architecture](docs/ARCHITECTURE.md) for the data flow and design notes.

## Guidelines

- Keep `web/index.html` dependency-free and directly openable as a file. Browser mode must degrade gracefully when the backend is absent.
- Treat telemetry as untrusted: validate lengths, resynchronise framing, and never let a corrupt frame update instruments or crash the loop.
- Do not duplicate protocol parsing in the backend unless the backend actually needs the decoded value. The display owns instrument state.
- The backend owns the link and the log. Any change that could close, reopen or block the link needs particular care.
- Preserve the existing user-facing colour language: white values, green normal, magenta guidance, cyan pilot settings, amber caution, red warning/failure.
- Update [the user guide](docs/USER_GUIDE.md) and [quick reference](docs/QUICK_REFERENCE.md) when behaviour or defaults change.
- Do not commit logs, settings, virtual environments, build output or desktop metadata — `.gitignore` covers these.

## Licensing

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE). Third-party dependencies keep their own licenses.
