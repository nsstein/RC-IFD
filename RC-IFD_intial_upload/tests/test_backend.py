#!/usr/bin/env python3
"""
Backend tests — run:  python tests/test_backend.py
Covers: MAVLink framer, recorder (files, tlog, sidecar, short-log discard, pruning, crash recovery),
server control channel + WebSocket data path over a UDP link, and serial unplug/re-enumeration
recovery using a fake pyserial backed by real pseudo-terminals (POSIX only for the serial part).
"""
import asyncio, base64, json, os, shutil, socket, struct, sys, tempfile, threading, time, types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

FAIL = []


def ok(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


# ------------------------------------------------------------------ fake pyserial (pty-backed)
def install_fake_serial():
    if os.name != "posix":
        return None
    import pty, tty
    state = {"ports": {}, "unplugged": set()}

    class SerialException(OSError):
        pass

    class Serial:
        def __init__(self, port=None, baudrate=9600, timeout=None, **kw):
            self.port, self.baudrate, self.timeout = port, baudrate, timeout
            self.fd = None
            self.dtr = self.rts = True
            self.write_timeout = None
            self.exclusive = False
            self.dtr_at_open = None
            if port:
                self.open()

        def open(self):
            if self.port in state["unplugged"] or self.port not in state["ports"]:
                raise SerialException(f"could not open port {self.port}: [Errno 2] No such file or directory")
            self.fd = os.open(state["ports"][self.port]["slave"], os.O_RDWR | os.O_NOCTTY)
            tty.setraw(self.fd)
            self.dtr_at_open = self.dtr
            state["ports"][self.port]["opened"].append(self)

        @property
        def in_waiting(self):
            if self.port in state["unplugged"]:
                raise SerialException("device reports readiness to read but returned no data (device disconnected?)")
            import fcntl, termios, array
            buf = array.array("i", [0])
            fcntl.ioctl(self.fd, termios.FIONREAD, buf)
            return buf[0]

        def read(self, n=1):
            import select
            if self.port in state["unplugged"]:
                raise SerialException("device disconnected")
            r, _, _ = select.select([self.fd], [], [], self.timeout or 0)
            if not r:
                return b""
            return os.read(self.fd, max(1, n))

        def write(self, b):
            if self.port in state["unplugged"]:
                raise SerialException("write failed")
            return os.write(self.fd, b)

        def reset_input_buffer(self):
            pass

        def cancel_read(self):
            pass

        def close(self):
            if self.fd is not None:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
                self.fd = None

    class PortInfo:
        def __init__(self, device, vid, pid, sn, desc):
            self.device, self.vid, self.pid, self.serial_number = device, vid, pid, sn
            self.description, self.manufacturer, self.product, self.hwid = desc, "Test", desc, f"USB VID:PID={vid:04X}:{pid:04X}"

    def comports():
        return [PortInfo(d, p["vid"], p["pid"], p["sn"], p["desc"]) for d, p in state["ports"].items()
                if d not in state["unplugged"]]

    def add_port(name, vid=0x0483, pid=0x5740, sn="ABC123", desc="STM32 Virtual ComPort"):
        m, s = pty.openpty()
        tty.setraw(m)
        os.set_blocking(m, False)
        state["ports"][name] = {"master": m, "slave": os.ttyname(s), "slave_fd": s, "vid": vid, "pid": pid, "sn": sn,
                                "desc": desc, "opened": []}
        return m

    mod = types.ModuleType("serial")
    mod.Serial, mod.SerialException, mod.__version__ = Serial, SerialException, "fake-pty"
    tools = types.ModuleType("serial.tools")
    lp = types.ModuleType("serial.tools.list_ports")
    lp.comports = comports
    tools.list_ports = lp
    mod.tools = tools
    sys.modules["serial"], sys.modules["serial.tools"], sys.modules["serial.tools.list_ports"] = mod, tools, lp
    return {"state": state, "add_port": add_port}


FAKE = install_fake_serial()

from ifd_backend import links, recorder, server          # noqa: E402
from telemetry_sim import Mav                             # noqa: E402


def hb_frames(n, mav=None):
    mav = mav or Mav()
    out = []
    for i in range(n):
        out.append(mav.frame(30, struct.pack("<Iffffff", i, 0.1, 0.2, 0.3, 0, 0, 0)))
        out.append(mav.frame(0, struct.pack("<IBBBBB", 5, 1, 3, 209, 4, 3)))
    return out


# ------------------------------------------------------------------ 1. framer
def test_framer():
    print("MAVLink framer")
    frames = hb_frames(200)
    stream = bytearray(b"".join(frames))
    # junk between frames + an unknown-id frame
    cut = len(frames[0]) + len(frames[1])              # insert at a frame boundary
    stream[cut:cut] = b"\x00\x13garbage\xFE"
    f = recorder.MavFramer()
    got = []
    for i in range(0, len(stream), 37):
        got += f.push(bytes(stream[i:i + 37]))
    ok(len(got) == 400, f"recovers 400 frames from chunked stream with junk (got {len(got)})")
    ok(all(g in frames for g in got), "all framed outputs are byte-identical to sent frames")


# ------------------------------------------------------------------ 2. recorder
def test_recorder(tmp):
    print("Recorder")
    d = os.path.join(tmp, "logs")
    r = recorder.Recorder(d, max_total_mb=1, min_free_mb=0, idle_split_s=60)
    r.set_source("mavlink", "unit-test")
    frames = hb_frames(300)
    for i in range(0, len(frames), 3):
        r.feed(b"".join(frames[i:i + 3]))
    r.event("arm", "ARMED")
    r.meta["start"] -= 10_000_000          # pretend the session lasted >3 s
    name = os.path.basename(r.base)
    r.stop("test")
    base = os.path.join(d, name)
    ok(os.path.exists(base + ".ifdrec") and os.path.exists(base + ".tlog") and os.path.exists(base + ".json"),
       "writes .ifdrec + .tlog + .json")
    with open(base + ".tlog", "rb") as fh:
        tl = fh.read()
    n, i = 0, 0
    while i < len(tl):
        ln = tl[i + 9]
        i += 8 + 12 + ln
        n += 1
    ok(n == 600, f"tlog contains every MAVLink frame (got {n})")
    meta = json.load(open(base + ".json"))
    ok(meta["state"] == "closed" and meta["armed"] and meta["events"][0]["v"] == "ARMED", "sidecar closed, armed flag + events")
    with open(base + ".ifdrec", "rb") as fh:
        raw = fh.read()
    ok(raw.startswith(b"IFDREC1 mavlink\n"), "ifdrec header")
    ok(len(r.list_logs()) == 1 and r.list_logs()[0]["armed"], "list_logs reports it")
    # short bench connect is discarded
    r.set_source("crsf", "x")
    r.feed(b"\xC8\x04\x07\x00\x00\x00")
    r.stop("bench")
    ok(len(r.list_logs()) == 1, "trivially short session discarded")
    # crash recovery
    r.set_source("msp", "x")
    r.feed(b"$M>" + b"\x00" * 3000)
    r._write_meta()
    crashed = os.path.basename(r.base)
    r.f.flush()
    r.f = None                              # simulate power loss (no stop())
    r2 = recorder.Recorder(d, max_total_mb=1, min_free_mb=0)
    st = {x["name"]: x["state"] for x in r2.list_logs()}
    ok(st.get(crashed) == "recovered", "unclosed log marked 'recovered' on next start")
    # pruning by size budget
    r3 = recorder.Recorder(d, max_total_mb=0, min_free_mb=0)
    r3.set_source("mavlink", "x")
    r3.feed(b"".join(frames))
    ok(len([x for x in r3.list_logs() if not x["active"]]) == 0, "oldest logs pruned when over budget")
    r3.stop()


# ------------------------------------------------------------------ WS client helper
class WS:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(f"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(1)
        assert b"101" in buf.split(b"\r\n")[0]
        self.rbuf = b""

    def send(self, data, op):
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack("!H", n))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def jsend(self, obj):
        self.send(json.dumps(obj).encode(), 1)

    def _read(self, n):
        while len(self.rbuf) < n:
            c = self.s.recv(65536)
            if not c:
                raise ConnectionError
            self.rbuf += c
        d, self.rbuf = self.rbuf[:n], self.rbuf[n:]
        return d

    def recv(self, timeout=5):
        self.s.settimeout(timeout)
        b0, b1 = self._read(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read(8))[0]
        return b0 & 0x0F, self._read(n)

    def until(self, pred, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            try:
                op, d = self.recv(max(0.05, end - time.time()))
            except (socket.timeout, TimeoutError):
                break
            m = json.loads(d) if op == 1 else d
            if pred(op, m):
                return m
        return None


def start_server(tmp):
    web = os.path.join(ROOT, "web")
    srv = server.IFDServer(web, os.path.join(tmp, "data"), port=18765, verbose=False)
    loop = asyncio.new_event_loop()
    ev = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(srv.start())
        ev.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ev.wait(5)
    return srv, loop


# ------------------------------------------------------------------ 3. server + UDP link
def test_server_udp(srv):
    print("Server + UDP link")
    import urllib.request
    page = urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/").read()
    ok(b"RC Instrument Flight Display v2.0" in page, "serves the UI")
    info = json.load(urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/api/info"))
    ok(info["app"] == "RC-IFD", "/api/info")
    rs = socket.create_connection(("127.0.0.1", srv.port)); rs.sendall(b"GET /../../../../etc/passwd HTTP/1.1\r\nHost: x\r\n\r\n")
    ok(rs.recv(64).startswith(b"HTTP/1.1 404"), "path traversal blocked"); rs.close()
    a = WS(srv.port)
    a.jsend({"op": "hello", "id": 1})
    h = a.until(lambda op, m: op == 1 and m.get("ev") == "info")
    ok(h and h["controller"], "first client is controller")
    b = WS(srv.port)
    b.jsend({"op": "hello", "id": 1})
    hb = b.until(lambda op, m: op == 1 and m.get("ev") == "info")
    ok(hb and not hb["controller"], "second client is read-only viewer")
    b.jsend({"op": "open", "id": 2, "cfg": {"kind": "udp", "udpPort": 14599, "proto": "mavlink"}})
    ok(b.until(lambda op, m: op == 1 and m.get("ev") == "error") is not None, "viewer cannot open links")
    a.jsend({"op": "open", "id": 3, "cfg": {"kind": "udp", "udpPort": 14599, "proto": "mavlink"}})
    ok(a.until(lambda op, m: op == 1 and m.get("ev") == "opened") is not None, "controller opens UDP link")
    veh = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    veh.bind(("127.0.0.1", 0))
    veh.settimeout(3)
    frames = hb_frames(150)
    for i in range(0, len(frames), 10):
        veh.sendto(b"".join(frames[i:i + 10]), ("127.0.0.1", 14599))
        time.sleep(0.01)
    got = bytearray()
    end = time.time() + 3
    total = sum(len(f) for f in frames)
    while len(got) < total and time.time() < end:
        try:
            op, d = a.recv(1)
        except Exception:
            break
        if op == 2:
            got += d
    ok(bytes(got) == b"".join(frames), f"controller receives exact byte stream ({len(got)}/{total})")
    gotb = bytearray()
    end = time.time() + 2
    while len(gotb) < total and time.time() < end:
        try:
            op, d = b.recv(1)
        except Exception:
            break
        if op == 2:
            gotb += d
    ok(len(gotb) == total, "viewer receives the same stream")
    a.send(b"\xFD\x01\x02\x03", 2)
    try:
        echo, _ = veh.recvfrom(100)
    except socket.timeout:
        echo = b""
    ok(echo == b"\xFD\x01\x02\x03", "controller bytes reach the vehicle (UDP reply to sender)")
    b.send(b"\xAA\xBB", 2)
    veh.settimeout(0.5)
    try:
        leak, _ = veh.recvfrom(100)
    except socket.timeout:
        leak = b""
    ok(leak == b"", "viewer bytes are NOT forwarded")
    a.jsend({"op": "event", "k": "arm", "v": "ARMED"})
    time.sleep(0.2)
    srv.rec.meta["start"] -= 10_000_000
    a.jsend({"op": "close", "id": 9})
    a.until(lambda op, m: op == 1 and m.get("ev") == "closed")
    a.jsend({"op": "logs", "id": 10})
    lg = a.until(lambda op, m: op == 1 and m.get("ev") == "logs")
    ok(lg and len(lg["list"]) == 1 and lg["list"][0]["armed"] and lg["list"][0]["tlog"], "session auto-logged with arm event")
    name = lg["list"][0]["name"]
    data = urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/logs/{name}.ifdrec").read()
    ok(data.startswith(b"IFDREC1 mavlink") and len(data) > total, "log downloadable for replay")
    a.jsend({"op": "settings_save", "id": 11, "settings": {"proto": "crsf", "port": "/dev/x"}})
    a.until(lambda op, m: op == 1 and m.get("ev") == "saved")
    a.jsend({"op": "hello", "id": 12})
    h2 = a.until(lambda op, m: op == 1 and m.get("ev") == "info")
    ok(h2["settings"].get("proto") == "crsf", "settings persisted in backend")
    a.s.close()
    time.sleep(0.3)
    b.jsend({"op": "hello", "id": 13})
    h3 = b.until(lambda op, m: op == 1 and m.get("ev") in ("info", "role"))
    ok(h3 is not None and (h3.get("controller") is True), "viewer promoted when controller leaves")
    b.s.close()


# ------------------------------------------------------------------ 4. serial (fake pyserial over pty)
def test_serial(srv, loop):
    if not FAKE:
        print("Serial: skipped (POSIX only)")
        return
    print("Serial link (pty-backed fake pyserial)")
    m1 = FAKE["add_port"]("/dev/ttyACM0")
    FAKE["add_port"]("/dev/ttyUSB0", vid=0x1A86, pid=0x7523, sn="", desc="USB Serial")
    ports = links.list_serial_ports()
    ok([p["device"] for p in ports] == ["/dev/ttyACM0", "/dev/ttyUSB0"], "lists ports")
    ok("STM32 flight controller" in ports[0]["label"] and "0483:5740" in ports[0]["label"], "labels known FC VID:PID")
    a = WS(srv.port)
    a.jsend({"op": "hello", "id": 1})
    a.until(lambda op, m: op == 1 and m.get("ev") == "info")
    a.jsend({"op": "open", "id": 2, "cfg": {"kind": "serial", "device": "/dev/ttyACM0", "baud": 420000, "proto": "crsf", "holdDtr": True}})
    ok(a.until(lambda op, m: op == 1 and m.get("ev") == "opened") is not None, "opens serial @ 420000")
    opened = FAKE["state"]["ports"]["/dev/ttyACM0"]["opened"][-1]
    ok(opened.dtr_at_open is False and opened.exclusive, "DTR held low before open + exclusive lock")
    os.write(m1, b"\xC8\x04\x07\x01\x02\x03" * 50)
    got = bytearray()
    end = time.time() + 2
    while len(got) < 300 and time.time() < end:
        try:
            op, d = a.recv(1)
        except Exception:
            break
        if op == 2:
            got += d
    ok(len(got) == 300, f"serial bytes reach UI ({len(got)}/300)")
    a.send(b"$M<\x00\x6c\x6c", 2)
    time.sleep(0.2)
    try:
        back = os.read(m1, 100)
    except BlockingIOError:
        back = b""
    ok(back == b"$M<\x00\x6c\x6c", "UI bytes written to serial (MSP poll path)")
    # unplug: device disappears; on replug it enumerates under a NEW name with the same VID/PID/serial
    FAKE["state"]["unplugged"].add("/dev/ttyACM0")
    lost = a.until(lambda op, m: op == 1 and m.get("ev") == "link" and m.get("state") == "lost", 3)
    ok(lost is not None, "unplug detected → 'lost'")
    m2 = FAKE["add_port"]("/dev/ttyACM1")
    back_on = a.until(lambda op, m: op == 1 and m.get("ev") == "link" and m.get("state") == "open", 5)
    ok(back_on is not None and "/dev/ttyACM1" in back_on.get("text", ""), "re-opens same adapter under its new name")
    os.write(m2, b"\xC8\x04\x07\x09\x09\x09")
    d = a.until(lambda op, m: op == 2, 2)
    ok(d == b"\xC8\x04\x07\x09\x09\x09", "data flows again after replug")
    a.jsend({"op": "open", "id": 3, "cfg": {"kind": "serial", "device": "/dev/nope", "baud": 115200, "proto": "msp"}})
    err = a.until(lambda op, m: op == 1 and m.get("ev") == "error")
    ok(err and "not found" in err["text"], f"friendly error for missing port: {err and err['text']}")
    a.jsend({"op": "close", "id": 4})
    a.s.close()


def main():
    tmp = tempfile.mkdtemp(prefix="ifdtest")
    try:
        test_framer()
        test_recorder(tmp)
        srv, loop = start_server(tmp)
        test_server_udp(srv)
        test_serial(srv, loop)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("ALL BACKEND TESTS PASSED" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}"))
    os._exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
