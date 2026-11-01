#!/usr/bin/env bash
# RC Instrument Flight Display - macOS / Linux launcher.  Extra args are passed through, e.g. ./run_mac_linux.sh --fullscreen
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: creating virtual environment..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt || .venv/bin/python -m pip install pyserial
fi
exec .venv/bin/python rc_ifd.py "$@"
