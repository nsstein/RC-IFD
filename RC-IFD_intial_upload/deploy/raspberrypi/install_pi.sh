#!/usr/bin/env bash
# Raspberry Pi (Pi 4 / Pi 5 / Pi 400, Raspberry Pi OS Bookworm desktop, 64-bit recommended)
#   bash deploy/raspberrypi/install_pi.sh            # install + autostart kiosk at login
#   bash deploy/raspberrypi/install_pi.sh --no-kiosk # install only
set -e
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$HOME/rc-ifd"
echo "== RC-IFD install from $SRC to $DEST"
sudo apt-get update
sudo apt-get install -y python3-venv python3-serial chromium-browser || sudo apt-get install -y python3-venv python3-serial chromium
mkdir -p "$DEST"
rsync -a --delete --exclude .venv --exclude __pycache__ "$SRC/" "$DEST/"
# venv that can also see the apt-installed pyserial
python3 -m venv --system-site-packages "$DEST/.venv"
"$DEST/.venv/bin/python" -c "import serial" 2>/dev/null || "$DEST/.venv/bin/python" -m pip install pyserial
# serial permissions + stop ModemManager grabbing flight controllers
sudo usermod -aG dialout "$USER"
sudo cp "$SRC/deploy/raspberrypi/99-rc-ifd.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# backend as a user service (starts at boot, logs even with no screen attached)
mkdir -p "$HOME/.config/systemd/user"
cp "$SRC/deploy/raspberrypi/rc-ifd.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now rc-ifd.service
sudo loginctl enable-linger "$USER"
if [ "$1" != "--no-kiosk" ]; then
  mkdir -p "$HOME/.config/autostart"
  cp "$SRC/deploy/raspberrypi/rc-ifd-kiosk.desktop" "$HOME/.config/autostart/"
  # labwc (Bookworm Wayland default) also reads ~/.config/labwc/autostart
  if [ -d "$HOME/.config/labwc" ] || command -v labwc >/dev/null; then
    mkdir -p "$HOME/.config/labwc"
    grep -q rc_ifd.py "$HOME/.config/labwc/autostart" 2>/dev/null || \
      echo "sh -c 'sleep 3; $DEST/.venv/bin/python $DEST/rc_ifd.py --kiosk' &" >> "$HOME/.config/labwc/autostart"
  fi
fi
cat <<MSG

== Done.
 * Log out/in (or reboot) once so the 'dialout' group applies.
 * Backend:  systemctl --user status rc-ifd     (logs to ~/.local/share/rc-ifd/logs)
 * Display:  starts full-screen at login; exit kiosk with Alt+F4.
 * GPIO UART (CRSF/MAVLink on GPIO14/15): sudo raspi-config → Interface Options → Serial Port →
   login shell: NO, hardware: YES.  Then pick /dev/serial0 in Settings.
 * Phones/tablets as extra read-only screens: add --lan to ExecStart in ~/.config/systemd/user/rc-ifd.service
MSG
