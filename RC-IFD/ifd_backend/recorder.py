"""
recorder.py — on-device flight logging for the RC Instrument Flight Display.

Every received byte is captured at the link (before the UI), so a log is complete even if the
display window is closed, frozen, or the UI is restarted mid-flight.

Files (in <data-dir>/logs):
  YYYYMMDD-HHMMSS_<proto>.ifdrec   all protocols — exact byte stream with µs timestamps
                                   ("IFDREC1 <proto>\\n" then [u64 BE µs][u32 BE len][bytes]…)
  YYYYMMDD-HHMMSS_mavlink.tlog     MAVLink only — standard tlog ([u64 BE µs][frame]…) that opens in
                                   Mission Planner, MAVExplorer, QGroundControl, pymavlink
  *.json                           sidecar: protocol, link, start/end, bytes, arm/mode events

Power-loss safety (Raspberry Pi on a field battery): data is flushed every second and fsync'd
every 5 s, so at most ~5 s is lost when power is pulled.  A log whose sidecar says "open" was
not closed cleanly; it is still replayable up to the last complete record.
"""
import json, os, shutil, struct, time

# CRC_EXTRA for MAVLink messages we may see a lot of; unknown IDs are framed by length + next-STX sync.
MAV_CRC_EXTRA = {0: 50, 1: 124, 2: 137, 24: 24, 30: 39, 33: 104, 65: 118, 66: 148, 74: 20, 76: 152,
                 109: 185, 147: 154, 168: 1, 242: 104, 253: 83, 22: 220, 27: 144, 29: 115, 36: 222,
                 42: 28, 62: 183, 77: 143, 116: 76, 125: 203, 136: 1, 152: 208, 163: 127, 178: 47,
                 193: 71, 241: 90, 111: 34, 4: 237, 32: 185, 31: 246, 35: 244, 46: 11, 49: 39}


def _x25(crc, b):
    t = (b ^ (crc & 0xFF)) & 0xFF
    t = (t ^ (t << 4)) & 0xFF
    return ((crc >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4)) & 0xFFFF


class MavFramer:
    """Splits a raw MAVLink byte stream into whole frames for .tlog output."""

    def __init__(self):
        self.buf = bytearray()

    def push(self, data):
        self.buf += data
        out = []
        b = self.buf
        i = 0
        n = len(b)
        while i < n:
            stx = b[i]
            if stx not in (0xFE, 0xFD):
                i += 1
                continue
            if n - i < 3:
                break
            ln = b[i + 1]
            v2 = stx == 0xFD
            if v2 and (b[i + 2] & 0xFE):
                i += 1
                continue
            hdr = 10 if v2 else 6
            total = hdr + ln + 2 + (13 if v2 and (b[i + 2] & 1) else 0)
            if n - i < total:
                break
            mid = (b[i + 7] | (b[i + 8] << 8) | (b[i + 9] << 16)) if v2 else b[i + 5]
            extra = MAV_CRC_EXTRA.get(mid)
            ok = False
            if extra is not None:
                c = 0xFFFF
                for k in range(i + 1, i + hdr + ln):
                    c = _x25(c, b[k])
                c = _x25(c, extra)
                ok = (b[i + hdr + ln] | (b[i + hdr + ln + 1] << 8)) == c
            else:
                ok = (n - i == total) or b[i + total] in (0xFE, 0xFD)
            if ok:
                out.append(bytes(b[i:i + total]))
                i += total
            else:
                i += 1
        del self.buf[:i]
        if len(self.buf) > 4096:
            del self.buf[:-512]
        return out


def now_us():
    return int(time.time() * 1e6)


class Recorder:
    def __init__(self, log_dir, max_total_mb=2000, min_free_mb=300, idle_split_s=60):
        self.dir = log_dir
        os.makedirs(self.dir, exist_ok=True)
        self.max_total = max_total_mb * 1024 * 1024
        self.min_free = min_free_mb * 1024 * 1024
        self.idle_split = idle_split_s
        self.auto = True
        self.manual = False
        self.proto = None
        self.link_desc = ""
        self.f = None
        self.tlog = None
        self.framer = None
        self.meta = None
        self.base = None
        self.last_rx = 0.0
        self.last_flush = 0.0
        self.last_sync = 0.0
        self.on_change = lambda: None
        self._recover_unclosed()

    # ------------------------------------------------------------ public
    @property
    def active(self):
        return self.f is not None

    def set_source(self, proto, link_desc):
        if self.active and proto != self.proto:
            self.stop("protocol changed")
        self.proto, self.link_desc = proto, link_desc

    def set_auto(self, on):
        self.auto = bool(on)
        if not on and not self.manual and self.active:
            self.stop("auto-log disabled")

    def set_manual(self, on):
        self.manual = bool(on)
        if on and not self.active and self.proto:
            self.start()
        if not on and self.active:
            self.stop("stopped by user")

    def feed(self, data):
        t = time.monotonic()
        if self.active and self.idle_split and not self.manual and t - self.last_rx > self.idle_split:
            self.stop("link idle — split")
        self.last_rx = t
        if not self.active:
            if not (self.auto or self.manual) or not self.proto:
                return
            self.start()
        ts = now_us()
        try:
            self.f.write(struct.pack(">QI", ts, len(data)))
            self.f.write(data)
            self.meta["bytes"] += len(data)
            if self.framer is not None:
                for fr in self.framer.push(data):
                    self.tlog.write(struct.pack(">Q", ts))
                    self.tlog.write(fr)
                    self.meta["frames"] += 1
        except OSError as e:
            self.meta["error"] = str(e)
            self.stop(f"write error: {e}")
            return
        self.meta["end"] = ts
        self._maybe_sync(t)

    def event(self, kind, text):
        """UI-reported events (arm/disarm, mode change, warnings) stored in the sidecar."""
        if self.active:
            ev = self.meta["events"]
            if len(ev) < 5000:
                ev.append({"t": now_us(), "k": kind, "v": text})
            if kind == "arm" and text == "ARMED":
                self.meta["armed"] = True

    def tick(self):
        """Called ~1 Hz from the server loop: flush, idle split, disk-space guard."""
        t = time.monotonic()
        if self.active:
            if self.idle_split and t - self.last_rx > self.idle_split and not self.manual:
                self.stop("link idle")
            else:
                self._maybe_sync(t)

    def stop(self, reason="closed"):
        if not self.active:
            return
        for fh in (self.f, self.tlog):
            if fh:
                try:
                    fh.flush()
                    os.fsync(fh.fileno())
                    fh.close()
                except OSError:
                    pass
        self.meta["state"] = "closed"
        self.meta["reason"] = reason
        self._write_meta()
        # discard trivially short logs (connect/disconnect on the bench) unless the user asked for it
        dur = (self.meta["end"] - self.meta["start"]) / 1e6 if self.meta["end"] else 0
        if not self.manual and (self.meta["bytes"] < 2048 or dur < 3):
            for ext in (".ifdrec", ".tlog", ".json"):
                try:
                    os.remove(self.base + ext)
                except OSError:
                    pass
        self.f = self.tlog = self.framer = None
        self.meta = None
        self.manual = False
        self.on_change()

    def status(self):
        if not self.active:
            return {"on": False, "auto": self.auto}
        return {"on": True, "auto": self.auto, "manual": self.manual, "file": os.path.basename(self.base),
                "bytes": self.meta["bytes"], "start": self.meta["start"]}

    def list_logs(self):
        out = []
        for n in os.listdir(self.dir):
            if not n.endswith(".ifdrec"):
                continue
            base = os.path.join(self.dir, n[:-7])
            try:
                st = os.stat(base + ".ifdrec")
            except OSError:
                continue
            meta = {}
            try:
                with open(base + ".json") as fh:
                    meta = json.load(fh)
            except Exception:
                pass
            out.append({"name": n[:-7], "size": st.st_size, "mtime": st.st_mtime,
                        "proto": meta.get("proto") or n[:-7].split("_")[-1],
                        "dur": ((meta.get("end") or 0) - (meta.get("start") or 0)) / 1e6 if meta.get("end") else None,
                        "armed": meta.get("armed", False), "state": meta.get("state", "?"),
                        "link": meta.get("link", ""), "tlog": os.path.exists(base + ".tlog"),
                        "active": self.active and base == self.base})
        out.sort(key=lambda x: x["mtime"], reverse=True)
        return out

    def path_for(self, name, ext=".ifdrec"):
        name = os.path.basename(name)
        p = os.path.join(self.dir, name + ext)
        return p if os.path.isfile(p) else None

    def delete(self, name):
        name = os.path.basename(name)
        if self.active and os.path.join(self.dir, name) == self.base:
            return False
        for ext in (".ifdrec", ".tlog", ".json"):
            try:
                os.remove(os.path.join(self.dir, name + ext))
            except OSError:
                pass
        return True

    # ------------------------------------------------------------ internals
    def start(self):
        self._prune()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.base = os.path.join(self.dir, f"{stamp}_{self.proto}")
        k = 1
        while os.path.exists(self.base + ".ifdrec"):
            self.base = os.path.join(self.dir, f"{stamp}-{k}_{self.proto}")
            k += 1
        self.f = open(self.base + ".ifdrec", "wb", buffering=256 * 1024)
        self.f.write(f"IFDREC1 {self.proto}\n".encode())
        if self.proto == "mavlink":
            self.tlog = open(self.base + ".tlog", "wb", buffering=256 * 1024)
            self.framer = MavFramer()
        self.meta = {"format": 1, "proto": self.proto, "link": self.link_desc, "start": now_us(), "end": None,
                     "bytes": 0, "frames": 0, "state": "open", "armed": False, "events": [], "app": "RC-IFD"}
        self._write_meta()
        t = time.monotonic()
        self.last_flush = self.last_sync = t
        self.last_rx = t
        self.on_change()

    def _maybe_sync(self, t):
        if t - self.last_flush >= 1.0:
            self.last_flush = t
            try:
                self.f.flush()
                if self.tlog:
                    self.tlog.flush()
            except OSError:
                pass
        if t - self.last_sync >= 5.0:
            self.last_sync = t
            try:
                os.fsync(self.f.fileno())
                if self.tlog:
                    os.fsync(self.tlog.fileno())
            except OSError:
                pass
            self._write_meta()
            if self._free() < self.min_free // 2:
                self.stop("disk nearly full")

    def _write_meta(self):
        if not self.meta:
            return
        tmp = self.base + ".json.tmp"
        try:
            with open(tmp, "w") as fh:
                json.dump(self.meta, fh)
            os.replace(tmp, self.base + ".json")
        except OSError:
            pass

    def _free(self):
        try:
            return shutil.disk_usage(self.dir).free
        except OSError:
            return 1 << 40

    def _prune(self):
        """Delete oldest logs while over the size budget or short of free space."""
        logs = sorted(self.list_logs(), key=lambda x: x["mtime"])
        total = sum(x["size"] for x in logs)
        while logs and (total > self.max_total or self._free() < self.min_free):
            x = logs.pop(0)
            total -= x["size"]
            self.delete(x["name"])

    def _recover_unclosed(self):
        """Mark logs left 'open' by a crash / power cut so the UI can flag them."""
        for n in os.listdir(self.dir):
            if n.endswith(".json"):
                p = os.path.join(self.dir, n)
                try:
                    with open(p) as fh:
                        m = json.load(fh)
                    if m.get("state") == "open":
                        m["state"] = "recovered"
                        with open(p, "w") as fh:
                            json.dump(m, fh)
                except Exception:
                    pass
