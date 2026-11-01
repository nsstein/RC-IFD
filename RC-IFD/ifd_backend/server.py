"""
server.py — local HTTP + WebSocket server that connects the instrument UI to the hardware.

  GET  /                    the instrument display (web/index.html)
  GET  /api/info            JSON: version, platform, pyserial, data dir
  GET  /logs/<name>.ifdrec  raw log for replay (also .tlog / .json)
  ADS-B: optional ground receiver (dump1090/readsb JSON or SBS-1) broadcast as {"ev":"adsb"} once per second
  WS   /ws                  binary frames = telemetry bytes (both directions)
                            text frames   = JSON control / events

Only the first UI client is the *controller*: its outbound bytes (MAVLink requests, MSP polls)
reach the vehicle.  Extra viewers (a phone or tablet on the same Wi-Fi when started with --lan)
receive the same telemetry but are read-only, so MSP polling is never duplicated.

Standard library only (+ pyserial for serial ports).
"""
import asyncio, base64, hashlib, json, mimetypes, os, platform, struct, sys, time, urllib.parse

from . import __version__
from .links import (HAVE_SERIAL, SERIAL_VERSION, SerialLink, TcpLink, UdpLink, list_serial_ports)
from .recorder import Recorder
from .adsb import AdsbFeed

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_WS_MSG = 4 * 1024 * 1024


def default_data_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "RC-IFD")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/RC-IFD")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "rc-ifd")


def is_raspberry_pi():
    try:
        with open("/proc/device-tree/model") as fh:
            return "raspberry pi" in fh.read().lower()
    except OSError:
        return False


def ws_frame(payload, opcode=2):
    n = len(payload)
    if n < 126:
        hdr = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 65536:
        hdr = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        hdr = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return hdr + payload


class Client:
    def __init__(self, writer, peer):
        self.w, self.peer = writer, peer
        self.controller = False

    def send_bin(self, data):
        try:
            if self.w.transport.get_write_buffer_size() > 2_000_000:   # stalled viewer: drop, never block flight data
                return
            self.w.write(ws_frame(data, 2))
        except Exception:
            pass

    def send_json(self, obj):
        try:
            self.w.write(ws_frame(json.dumps(obj).encode(), 1))
        except Exception:
            pass


class IFDServer:
    def __init__(self, web_dir, data_dir, host="127.0.0.1", port=8765, log_cfg=None, verbose=True):
        self.web_dir = web_dir
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.host, self.port = host, port
        self.verbose = verbose
        lc = log_cfg or {}
        self.rec = Recorder(os.path.join(data_dir, "logs"), max_total_mb=lc.get("max_total_mb", 2000),
                            min_free_mb=lc.get("min_free_mb", 300), idle_split_s=lc.get("idle_split_s", 60))
        self.rec.on_change = self._rec_changed
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.clients = []
        self.link = None
        self.link_cfg = None
        self.link_state = ("closed", "")
        self.loop = None
        self.server = None
        self.started = time.time()
        self.adsb = None

    def log(self, *a):
        if self.verbose:
            print(time.strftime("%H:%M:%S"), *a, flush=True)

    # ------------------------------------------------------------ lifecycle
    async def start(self):
        self.loop = asyncio.get_running_loop()
        last = None
        for p in range(self.port, self.port + 10):           # if the port is taken, try the next ones
            try:
                self.server = await asyncio.start_server(self._handle, self.host, p)
                self.port = p
                break
            except OSError as e:
                last = e
        if not self.server:
            raise OSError(f"no free port from {self.port}: {last}")
        s = self._settings()
        if "autoLog" in s:
            self.rec.set_auto(s["autoLog"])
        self.adsb = AdsbFeed(self.loop, lambda: None, self._adsb_status)
        if s.get("adsbKind", "none") != "none":
            await self.adsb.configure(s.get("adsbKind"), s.get("adsbAddr", ""))
        asyncio.ensure_future(self._ticker())
        self.log(f"[ifd] UI at http://{'localhost' if self.host in ('127.0.0.1', None) else self.host}:{self.port}/  "
                 f"data: {self.data_dir}  pyserial: {SERIAL_VERSION or 'MISSING'}")
        return self.port

    async def stop(self):
        if self.adsb:
            await self.adsb.stop()
        await self._close_link()
        self.rec.stop("app exit")
        if self.server:
            self.server.close()

    async def _ticker(self):
        k = 0
        while True:
            await asyncio.sleep(1)
            k += 1
            self.rec.tick()
            if self.adsb and self.adsb.kind != "none" and self.clients:
                self._broadcast_json({"ev": "adsb", "src": self.adsb.kind.upper(), "status": self.adsb.status,
                                      "list": self.adsb.snapshot()})
            if self.link and k % 2 == 0:
                st = {"ev": "stats", "rx": self.link.rx, "tx": self.link.tx}
                if self.rec.active:
                    st["rec"] = self.rec.status()
                self._broadcast_json(st)

    # ------------------------------------------------------------ link management
    def _on_rx(self, data):
        self.rec.feed(data)
        for c in self.clients:
            c.send_bin(data)

    def _on_state(self, state, text):
        self.link_state = (state, text)
        self.log(f"[link] {state}: {text}")
        self._broadcast_json({"ev": "link", "state": state, "text": text, "cfg": self.link_cfg})

    async def _close_link(self):
        if self.link:
            l, self.link = self.link, None
            try:
                await l.close()
            except Exception:
                pass
            self.rec.stop("link closed")
        self.link_cfg = None
        self.link_state = ("closed", "")

    async def _open_link(self, cfg):
        await self._close_link()
        kind = cfg.get("kind")
        proto = cfg.get("proto", "mavlink")
        if kind == "serial":
            dev = cfg.get("device")
            if not dev:
                raise RuntimeError("No serial port selected")
            link = SerialLink(self.loop, self._on_rx, self._on_state, dev, cfg.get("baud", 115200),
                              hold_dtr=cfg.get("holdDtr", True))
        elif kind == "udp":
            tgt = None
            if cfg.get("udpTarget"):
                h, p = str(cfg["udpTarget"]).rsplit(":", 1)
                tgt = (h, int(p))
            link = UdpLink(self.loop, self._on_rx, self._on_state, cfg.get("udpPort", 14550), tgt)
        elif kind == "tcp":
            link = TcpLink(self.loop, self._on_rx, self._on_state, cfg.get("tcpHost", "192.168.4.1"), cfg.get("tcpPort", 5760))
        else:
            raise RuntimeError(f"unknown link kind {kind!r}")
        self.link, self.link_cfg = link, cfg
        self.rec.set_source(proto, link.desc)
        try:
            await link.open()
        except Exception:
            self.link, self.link_cfg = None, None
            raise

    def _adsb_status(self, st):
        self.log(f"[adsb] {st}")
        self._broadcast_json({"ev": "adsb", "src": self.adsb.kind.upper() if self.adsb else "", "status": st, "list": []})

    # ------------------------------------------------------------ recorder notifications
    def _rec_changed(self):
        self._broadcast_json({"ev": "rec", **self.rec.status()})

    # ------------------------------------------------------------ settings
    def _settings(self):
        try:
            with open(self.settings_path) as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_settings(self, s):
        tmp = self.settings_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(s, fh, indent=1)
        os.replace(tmp, self.settings_path)

    # ------------------------------------------------------------ control channel
    def _info(self, client=None):
        return {"ev": "info", "app": "RC-IFD", "version": __version__, "platform": sys.platform,
                "machine": platform.machine(), "pi": is_raspberry_pi(), "python": platform.python_version(),
                "pyserial": SERIAL_VERSION, "dataDir": self.data_dir, "logDir": self.rec.dir,
                "controller": bool(client and client.controller), "lan": self.host not in ("127.0.0.1", "localhost", None),
                "link": {"state": self.link_state[0], "text": self.link_state[1], "cfg": self.link_cfg},
                "rec": self.rec.status(),
                "adsb": {"kind": self.adsb.kind if self.adsb else "none", "status": self.adsb.status if self.adsb else "off"}}

    async def _control(self, client, msg):
        op = msg.get("op")
        rid = msg.get("id")
        reply = lambda obj: client.send_json({**obj, "id": rid})
        if op == "hello":
            reply({**self._info(client), "settings": self._settings()})
        elif op == "ports":
            ports = await self.loop.run_in_executor(None, list_serial_ports)
            reply({"ev": "ports", "list": ports, "pyserial": HAVE_SERIAL})
        elif op == "open":
            if not client.controller:
                return reply({"ev": "error", "text": "This display is a read-only viewer"})
            try:
                await self._open_link(msg.get("cfg") or {})
                reply({"ev": "opened", "desc": self.link.desc if self.link else ""})
            except Exception as e:
                self._on_state("error", str(e))
                reply({"ev": "error", "text": str(e)})
        elif op == "close":
            if client.controller:
                await self._close_link()
                self._broadcast_json({"ev": "link", "state": "closed", "text": "Disconnected", "cfg": None})
            reply({"ev": "closed"})
        elif op == "rec":
            if client.controller:
                if self.link_cfg:
                    self.rec.set_source(self.link_cfg.get("proto", "mavlink"), self.link.desc if self.link else "")
                self.rec.set_manual(bool(msg.get("on")))
            reply({"ev": "rec", **self.rec.status()})
        elif op == "autolog":
            self.rec.set_auto(bool(msg.get("on")))
            s = self._settings()
            s["autoLog"] = bool(msg.get("on"))
            self._save_settings(s)
            reply({"ev": "rec", **self.rec.status()})
        elif op == "event":
            self.rec.event(str(msg.get("k", ""))[:32], str(msg.get("v", ""))[:200])
        elif op == "logs":
            reply({"ev": "logs", "list": self.rec.list_logs(), "dir": self.rec.dir})
        elif op == "log_delete":
            ok = self.rec.delete(msg.get("name", ""))
            reply({"ev": "logs", "list": self.rec.list_logs(), "dir": self.rec.dir, "deleted": ok})
        elif op == "settings_save":
            s = msg.get("settings")
            if isinstance(s, dict):
                s["autoLog"] = self.rec.auto
                self._save_settings(s)
            reply({"ev": "saved"})
        elif op == "adsb_cfg":
            if client.controller and self.adsb:
                await self.adsb.configure(str(msg.get("kind", "none")), str(msg.get("addr", ""))[:300])
            reply({"ev": "ok"})
        elif op == "open_folder":
            self._open_folder(self.rec.dir)
            reply({"ev": "ok"})
        else:
            reply({"ev": "error", "text": f"unknown op {op!r}"})

    def _open_folder(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa
            elif sys.platform == "darwin":
                os.spawnlp(os.P_NOWAIT, "open", "open", path)
            else:
                os.spawnlp(os.P_NOWAIT, "xdg-open", "xdg-open", path)
        except Exception:
            pass

    def _broadcast_json(self, obj):
        for c in self.clients:
            c.send_json(obj)

    def _assign_controller(self):
        if self.clients and not any(c.controller for c in self.clients):
            # prefer a local client so a LAN viewer can never take control of the link
            local = [c for c in self.clients if c.peer and c.peer[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")]
            c = (local or self.clients)[0]
            c.controller = True
            c.send_json({"ev": "role", "controller": True})

    # ------------------------------------------------------------ HTTP / WS plumbing
    async def _handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except Exception:
            writer.close()
            return
        lines = head.decode("latin1").split("\r\n")
        parts = lines[0].split(" ")
        path = urllib.parse.unquote(parts[1] if len(parts) > 1 else "/").split("?")[0]
        hdrs = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
        if hdrs.get("upgrade", "").lower() == "websocket" and path == "/ws":
            await self._ws(reader, writer, hdrs)
            return
        try:
            await self._http(writer, path)
        except Exception as e:
            self.log("[http]", e)
        try:
            await writer.drain()
            writer.close()
        except Exception:
            pass

    def _send_http(self, writer, code, body, ctype="text/plain; charset=utf-8", extra=""):
        reason = {200: "OK", 404: "Not Found", 403: "Forbidden"}.get(code, "OK")
        writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     f"Cache-Control: no-store\r\nConnection: close\r\n{extra}\r\n".encode() + body)

    async def _http(self, writer, path):
        if path in ("/", "/index.html"):
            path = "/index.html"
        if path == "/api/info":
            return self._send_http(writer, 200, json.dumps(self._info()).encode(), "application/json")
        if path.startswith("/logs/"):
            name = os.path.basename(path[6:])
            base, ext = os.path.splitext(name)
            fp = self.rec.path_for(base, ext) if ext in (".ifdrec", ".tlog", ".json") else None
            if not fp:
                return self._send_http(writer, 404, b"no such log")
            with open(fp, "rb") as fh:
                body = fh.read()
            return self._send_http(writer, 200, body, "application/octet-stream",
                                   f'Content-Disposition: attachment; filename="{name}"\r\n')
        # static files from web/ (no directory traversal)
        fp = os.path.realpath(os.path.join(self.web_dir, path.lstrip("/")))
        if not fp.startswith(os.path.realpath(self.web_dir)) or not os.path.isfile(fp):
            return self._send_http(writer, 404, b"not found")
        with open(fp, "rb") as fh:
            body = fh.read()
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send_http(writer, 200, body, ctype)

    async def _ws(self, reader, writer, hdrs):
        key = hdrs.get("sec-websocket-key", "").encode()
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
        writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        peer = writer.get_extra_info("peername")
        c = Client(writer, peer)
        self.clients.append(c)
        self._assign_controller()
        self.log(f"[ws] client {peer} connected ({len(self.clients)}){' — controller' if c.controller else ' — viewer'}")
        frag_op, frag = None, bytearray()
        try:
            while True:
                b0, b1 = await reader.readexactly(2)
                fin, op, masked, n = b0 & 0x80, b0 & 0x0F, b1 & 0x80, b1 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", await reader.readexactly(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", await reader.readexactly(8))[0]
                if n > MAX_WS_MSG:
                    break
                mask = await reader.readexactly(4) if masked else b"\0\0\0\0"
                data = bytearray(await reader.readexactly(n))
                if masked and n:
                    m = int.from_bytes(mask * (n // 4 + 1), "little")
                    data = bytearray((int.from_bytes(data, "little") ^ (m & ((1 << (8 * n)) - 1))).to_bytes(n, "little"))
                if op == 8:
                    writer.write(ws_frame(b"", 8))
                    break
                if op == 9:
                    writer.write(ws_frame(bytes(data), 10))
                    continue
                if op == 10:
                    continue
                if op in (1, 2) and not fin:
                    frag_op, frag = op, bytearray(data)
                    continue
                if op == 0:
                    frag += data
                    if not fin:
                        continue
                    op, data = frag_op, frag
                if op == 2:
                    if c.controller and self.link and data:
                        self.link.write(bytes(data))
                elif op == 1:
                    try:
                        msg = json.loads(data.decode("utf-8"))
                    except Exception:
                        continue
                    await self._control(c, msg)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as e:
            self.log("[ws] error", e)
        finally:
            if c in self.clients:
                self.clients.remove(c)
            was_ctl = c.controller
            self.log(f"[ws] client {peer} disconnected ({len(self.clients)})")
            if was_ctl:
                self._assign_controller()
            try:
                writer.close()
            except Exception:
                pass
