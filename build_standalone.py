#!/usr/bin/env python3
"""
Build a standalone app with PyInstaller (no Python needed on the target machine).
  pip install pyinstaller pyserial pywebview
  python3 build_standalone.py
macOS  : dist/RC-IFD.app   (custom icon: RC-IFD.icns in this folder)
Windows: dist/RC-IFD/RC-IFD.exe (custom icon: RC-IFD.ico in this folder)
Linux  : dist/RC-IFD/RC-IFD
Build on each target OS - PyInstaller does not cross-compile.
"""
import os, plistlib, shutil, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)
sep = ";" if os.name == "nt" else ":"
mac = sys.platform == "darwin"

for d in ("build", "dist"):
    shutil.rmtree(d, ignore_errors=True)
if os.path.exists("RC-IFD.spec"):
    os.remove("RC-IFD.spec")

args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--name", "RC-IFD", "--windowed",
        "--add-data", f"{os.path.join(here, 'web')}{sep}web",
        "--hidden-import", "serial.tools.list_ports"]

icon = os.path.join(here, "RC-IFD.icns" if mac else "RC-IFD.ico")
if os.path.isfile(icon):
    args += ["--icon", icon]
    print("Using icon:", icon)
else:
    print("WARNING: no custom icon found at", icon)
if mac:
    args += ["--osx-bundle-identifier", "com.rcifd.instrumentdisplay"]
args.append(os.path.join(here, "rc_ifd.py"))

print(" ".join(args))
rc = subprocess.call(args)
if rc:
    sys.exit(rc)

if mac:
    app = os.path.join(here, "dist", "RC-IFD.app")
    if os.path.isfile(icon):
        # guarantee the icon is in the bundle and referenced by Info.plist
        shutil.copy(icon, os.path.join(app, "Contents", "Resources", "RC-IFD.icns"))
        plist = os.path.join(app, "Contents", "Info.plist")
        with open(plist, "rb") as fh:
            info = plistlib.load(fh)
        info["CFBundleIconFile"] = "RC-IFD.icns"
        with open(plist, "wb") as fh:
            plistlib.dump(info, fh)
    # re-sign AFTER any change to the bundle, or macOS refuses to open it
    subprocess.call(["codesign", "--force", "--deep", "--sign", "-", app])
    os.utime(app, None)
    print("Built:", app)
else:
    print("Built:", os.path.join(here, "dist", "RC-IFD"))
