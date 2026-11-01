"""
links.py — telemetry transports for the RC Instrument Flight Display desktop backend.

Every link pushes received bytes to `on_rx(bytes)` (called on the asyncio loop) and
reports state changes through `on_state(state, text)`:
    "opening" | "open" | "lost" | "reconnecting" | "error" | "closed"
`write(bytes)` sends display->vehicle traffic (MAVLink heartbeats / stream requests, MSP polls).

Serial notes (field-tested failure modes this code guards against):
  * Device unplug / brown-out: reader thread catches the OSError, reports "lost", and re-opens
    the same port — matched by path first, then by USB VID:PID + serial number, because
    Windows can re-enumerate a different COMn and macOS/Linux can change ttyACMn / cu.* names.
  * DTR/RTS: opening a port normally pulses DTR, which resets ESP32 / Arduino / CP210x-based
    devices (ELRS backpacks, ESP telemetry bridges).  `hold_dtr` keeps both lines de-asserted.
  * Exclusive access on POSIX so a second program (Mission Planner, configurator) can't silently
    steal half the bytes.
  * Non-standard baud rates (CRSF 416666 / 420000) are passed straight to pyserial, which uses
    termios2 (Linux / Raspberry Pi) or IOSSIOSPEED (macOS).
"""
import asyncio, os, sys, threading, time

try:
    import serial                       # pyserial
    import serial.tools.list_ports as list_ports
    HAVE_SERIAL = True
    SERIAL_VERSION = getattr(serial, "__version__", "?")
except Exception:                       # pragma: no cover - reported to the UI
    serial = None
    list_ports = None
    HAVE_SERIAL = False
    SERIAL_VERSION = None


# ---------------------------------------------------------------- port discovery
def _by_id_map():
    """Linux: map /dev/ttyACM0 -> /dev/serial/by-id/usb-... (stable across replug)."""
    out = {}
    d = "/dev/serial/by-id"
    if os.path.isdir(d):
        for n in os.listdir(d):
            p = os.path.join(d, n)
            try:
                out[os.path.realpath(p)] = p
            except OSError:
                pass
    return out


def list_serial_ports():
    """Return ports suitable for a dropdown, best candidates first."""
    if not HAVE_SERIAL:
        return []
    by_id = _by_id_map()
    ports = []
    for p in list_ports.comports():
        dev = p.device
        # macOS exposes every port twice; /dev/tty.* blocks on open waiting for DCD — use /dev/cu.*
        if sys.platform == "darwin" and dev.startswith("/dev/tty."):
            continue
        # skip Bluetooth-incoming and debug consoles that are never telemetry
        low = (dev + " " + (p.description or "")).lower()
        if "bluetooth-incoming" in low or "debug-console" in low:
            continue
        usb = p.vid is not None
        item = {
            "device": dev,
            "desc": p.description if p.description and p.description != "n/a" else "",
            "manufacturer": p.manufacturer or "",
            "product": p.product or "",
            "vid": p.vid, "pid": p.pid,
            "serial": p.serial_number or "",
            "hwid": p.hwid or "",
            "usb": usb,
            "byId": by_id.get(os.path.realpath(dev), "") if os.name == "posix" else "",
        }
        item["label"] = _label(item)
        ports.append(item)
    # Raspberry Pi GPIO UART (not a USB device, so comports() may list it as ttyAMA0 / ttyS0)
    if sys.platform.startswith("linux") and os.path.exists("/dev/serial0"):
        real = os.path.realpath("/dev/serial0")
        if not any(os.path.realpath(x["device"]) == real for x in ports if x["device"] != "/dev/serial0"):
            ports.append({"device": "/dev/serial0", "desc": "Raspberry Pi GPIO UART (GPIO14/15)", "manufacturer": "",
                          "product": "", "vid": None, "pid": None, "serial": "", "hwid": "", "usb": False, "byId": "",
                          "label": "/dev/serial0 — Raspberry Pi GPIO UART (GPIO14/15)"})
        else:
            for x in ports:
                if os.path.realpath(x["device"]) == real:
                    x["desc"] = (x["desc"] + " — Pi GPIO UART (serial0)").strip(" —")
                    x["label"] = _label(x)
    ports.sort(key=lambda x: (not x["usb"], x["device"]))
    return ports


KNOWN_USB = {
    (0x0483, 0x5740): "STM32 flight controller (VCP)",
    (0x0483, 0xDF11): "STM32 in DFU mode — not telemetry",
    (0x10C4, 0xEA60): "CP210x USB-UART",
    (0x0403, 0x6001): "FTDI FT232R", (0x0403, 0x6015): "FTDI FT231X",
    (0x1A86, 0x7523): "CH340 USB-UART", (0x1A86, 0x55D4): "CH9102 USB-UART",
    (0x2DAE, 0x1011): "CubePilot / Hex", (0x2DAE, 0x1016): "CubePilot Orange",
    (0x26AC, 0x0011): "Pixhawk (PX4 FMU)", (0x1209, 0x5740): "ArduPilot ChibiOS FC",
    (0x303A, 0x1001): "ESP32-S3/C3 native USB",
}


def _label(p):
    vidpid = f"{p['vid']:04X}:{p['pid']:04X}" if p["vid"] is not None else ""
    known = KNOWN_USB.get((p["vid"], p["pid"]), "")
    name = p["product"] or p["desc"] or known
    if known and known not in name:
        name = f"{name} ({known})" if name else known
    bits = [p["device"]]
    if name:
        bits.append(name)
    if vidpid:
        bits.append(vidpid)
    return " — ".join(bits)


def _find_same_device(info):
    """After an unplug, locate the same physical adapter even if its device name changed."""
    if not HAVE_SERIAL:
        return None
    ports = list(list_ports.comports())
    for p in ports:
        if p.device == info.get("device"):
            return p.device
    if info.get("vid") is not None:
        cands = [p for p in ports if p.vid == info["vid"] and p.pid == info["pid"]]
        if info.get("serial"):
            exact = [p for p in cands if (p.serial_number or "") == info["serial"]]
            if exact:
                cands = exact
        if sys.platform == "darwin":
            cands = [p for p in cands if not p.device.startswith("/dev/tty.")] or cands
        if len(cands) == 1 or (cands and info.get("serial")):
            return cands[0].device
    if info.get("byId") and os.path.exists(info["byId"]):
        return info["byId"]
    return None


def _port_info(dev):
    if not HAVE_SERIAL:
        return {"device": dev}
    real = os.path.realpath(dev) if os.name == "posix" else dev
    for p in list_ports.comports():
        if p.device == dev or (os.name == "posix" and os.path.realpath(p.device) == real):
            return {"device": dev, "vid": p.vid, "pid": p.pid, "serial": p.serial_number or "",
                    "byId": _by_id_map().get(real, "") if os.name == "posix" else ""}
    return {"device": dev}


def friendly_serial_error(e, dev):
    msg = str(e)
    low = msg.lower()
    if isinstance(e, PermissionError) or "permission denied" in low or "access is denied" in low:
        if sys.platform.startswith("linux"):
            return (f"Permission denied on {dev}. Add your user to the 'dialout' group "
                    f"(sudo usermod -aG dialout $USER) then log out/in.")
        if os.name == "nt":
            return f"{dev} is in use by another program (Mission Planner, configurator, Arduino) — close it and retry."
        return f"Permission denied on {dev}: {msg}"
    if "busy" in low or "resource temporarily unavailable" in low or "could not exclusively lock" in low:
        return f"{dev} is busy — another program has it open (configurator / GCS / ModemManager)."
    if "no such file" in low or "could not open port" in low or "filenotfounderror" in low or "cannot find" in low:
        return f"{dev} not found — unplugged, or the driver (CH340/CP210x) is not installed."
    if "baud" in low or "invalid argument" in low:
        return f"{dev}: baud rate not supported by this adapter ({msg}). Try 416666 or an FTDI/CP2102N adapter."
    return f"{dev}: {msg}"


# ---------------------------------------------------------------- base
class Link:
    kind = "base"

    def __init__(self, loop, on_rx, on_state):
        self.loop, self.on_rx, self.on_state = loop, on_rx, on_state
        self.alive = True
        self.tx = 0
        self.rx = 0
        self.desc = ""

    def _state(self, s, text=""):
        self.loop.call_soon_threadsafe(self.on_state, s, text)

    def _rx(self, data):
        self.rx += len(data)
        self.on_rx(data)

    async def open(self):
        raise NotImplementedError

    def write(self, data):
        pass

    async def close(self):
        self.alive = False


# ---------------------------------------------------------------- serial
class SerialLink(Link):
    kind = "serial"

    def __init__(self, loop, on_rx, on_state, device, baud, hold_dtr=True):
        super().__init__(loop, on_rx, on_state)
        self.device, self.baud, self.hold_dtr = device, int(baud), hold_dtr
        self.ser = None
        self.lock = threading.Lock()
        self.info = _port_info(device)
        self.desc = f"serial {device} @ {self.baud}"
        self.thread = None

    def _open_port(self, dev):
        s = serial.Serial()
        s.port = dev
        s.baudrate = self.baud
        s.timeout = 0.02                 # read() returns at most every 20 ms -> low display latency
        s.write_timeout = 0.5
        if self.hold_dtr:
            s.dtr = False                # set before open(): suppresses the reset pulse on ESP32/Arduino
            s.rts = False
        if os.name == "posix":
            try:
                s.exclusive = True
            except Exception:
                pass
        s.open()
        try:
            s.reset_input_buffer()
        except Exception:
            pass
        return s

    async def open(self):
        if not HAVE_SERIAL:
            raise RuntimeError("pyserial is not installed — run:  pip install pyserial")
        self._state("opening", f"Opening {self.device} @ {self.baud}…")
        try:
            self.ser = await self.loop.run_in_executor(None, self._open_port, self.device)
        except Exception as e:
            raise RuntimeError(friendly_serial_error(e, self.device))
        self._state("open", f"Serial {self.device} @ {self.baud} baud")
        self.thread = threading.Thread(target=self._reader, name="serial-rx", daemon=True)
        self.thread.start()

    def _reader(self):
        while self.alive:
            s = self.ser
            if s is None:
                if not self._reopen():
                    return
                continue
            try:
                n = s.in_waiting
                data = s.read(n if n else 1)
                if n == 0 and data:      # got the first byte; grab whatever else already arrived
                    more = s.in_waiting
                    if more:
                        data += s.read(more)
                if data:
                    self.loop.call_soon_threadsafe(self._rx, data)
            except Exception as e:       # unplug, brown-out, driver reset
                if not self.alive:
                    return
                with self.lock:
                    try:
                        s.close()
                    except Exception:
                        pass
                    self.ser = None
                self._state("lost", f"Serial lost ({e.__class__.__name__}) — waiting for device…")

    def _reopen(self):
        last_err = ""
        while self.alive:
            time.sleep(1.0)
            dev = _find_same_device(self.info)
            if not dev:
                continue
            try:
                s = self._open_port(dev)
            except Exception as e:
                msg = friendly_serial_error(e, dev)
                if msg != last_err:
                    self._state("reconnecting", msg)
                    last_err = msg
                continue
            with self.lock:
                self.ser = s
            if dev != self.device:
                self.device = dev
                self.desc = f"serial {dev} @ {self.baud}"
            self._state("open", f"Serial re-opened {dev} @ {self.baud} baud")
            return True
        return False

    def write(self, data):
        with self.lock:
            s = self.ser
            if s is None:
                return
            try:
                s.write(data)
                self.tx += len(data)
            except Exception:
                pass                     # the reader thread handles loss

    async def close(self):
        self.alive = False
        with self.lock:
            s, self.ser = self.ser, None
        if s:
            try:
                s.cancel_read()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        if self.thread:
            await self.loop.run_in_executor(None, self.thread.join, 1.0)


# ---------------------------------------------------------------- UDP
class UdpLink(Link):
    """Listen for UDP telemetry (MAVProxy --out, mavlink-router, ESP32/ELRS WiFi backpack, SITL).
    Replies go to the most recent sender, or to a fixed --udp-out style target."""
    kind = "udp"

    def __init__(self, loop, on_rx, on_state, port, target=None):
        super().__init__(loop, on_rx, on_state)
        self.port, self.target = int(port), target
        self.peer = target
        self.transport = None
        self.desc = f"UDP :{self.port}"

    async def open(self):
        link = self

        class P(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                if link.target is None and link.peer != addr:
                    link.peer = addr
                    link.on_state("open", f"UDP :{link.port} receiving from {addr[0]}:{addr[1]}")
                link._rx(data)

            def error_received(self, exc):
                pass

        try:
            self.transport, _ = await self.loop.create_datagram_endpoint(P, local_addr=("0.0.0.0", self.port))
        except OSError as e:
            raise RuntimeError(f"UDP port {self.port} unavailable: {e.strerror or e}")
        self.on_state("open", f"UDP listening on :{self.port}")

    def write(self, data):
        if self.transport and self.peer:
            try:
                self.transport.sendto(data, self.peer)
                self.tx += len(data)
            except Exception:
                pass

    async def close(self):
        self.alive = False
        if self.transport:
            self.transport.close()
            await asyncio.sleep(0.05)        # let the loop release the socket before a re-open on the same port


# ---------------------------------------------------------------- TCP
class TcpLink(Link):
    """Connect to a TCP telemetry server (ESP32 WiFi bridges on :5760, SITL, mavlink-router)."""
    kind = "tcp"

    def __init__(self, loop, on_rx, on_state, host, port):
        super().__init__(loop, on_rx, on_state)
        self.host, self.port = host, int(port)
        self.writer = None
        self.task = None
        self.desc = f"TCP {host}:{self.port}"

    async def open(self):
        self.task = asyncio.ensure_future(self._run())

    async def _run(self):
        backoff = 1.0
        while self.alive:
            self.on_state("opening", f"Connecting TCP {self.host}:{self.port}…")
            try:
                r, w = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 5)
                self.writer = w
                backoff = 1.0
                self.on_state("open", f"TCP connected {self.host}:{self.port}")
                while self.alive:
                    data = await r.read(4096)
                    if not data:
                        break
                    self._rx(data)
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.on_state("reconnecting", f"TCP {self.host}:{self.port} — {e.__class__.__name__}: {e}")
            self.writer = None
            if self.alive:
                self.on_state("lost", f"TCP lost — retry in {backoff:.0f} s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8)

    def write(self, data):
        if self.writer:
            try:
                self.writer.write(data)
                self.tx += len(data)
            except Exception:
                pass

    async def close(self):
        self.alive = False
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
        if self.task:
            self.task.cancel()
