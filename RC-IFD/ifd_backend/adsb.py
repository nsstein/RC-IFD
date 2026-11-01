"""
adsb.py — ADS-B traffic for the RC Instrument Flight Display (desktop backend).

Several sources can run at the same time; targets are merged and de-duplicated by ICAO address
(the freshest position wins), so an aircraft heard by two feeds appears once.

  Online services (internet aggregators, queried around a moving point):
    built-in   adsb.lol   https://api.adsb.lol/v2/point/{lat}/{lon}/{dist}
               adsb.fi    https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{dist}
    custom     any ADSBExchange-v2-compatible URL template, optional API key
  Local receiver (no internet):
    json       dump1090 / readsb / tar1090 / PiAware  aircraft.json URL (no placeholders needed)
    sbs        SBS-1 / BaseStation TCP stream, host:30003

URL template placeholders (case-insensitive):
    {lat} {lon}        query centre, decimal degrees (6 dp)
    {dist} {dist_nm}   radius in nautical miles (integer, 1–250 — the unit the aggregator APIs use)
    {dist_km} {dist_m} radius in kilometres / metres
    {key}              the row's API key
The query centre is the aircraft's live position, then its last known position, then home — sent by the
display every few seconds.  Until a position is known, online services wait ("waiting for position").

Why a service can return HTTP 403:
  * the service blocks generic / library User-Agents (adsb.lol does) — we send a descriptive one;
  * the service sits behind a bot firewall that blocks API clients (api.adsb.one) — nothing a client
    can do; Kite-GC ships adsb.one disabled for the same reason;
  * the service now requires approval / an API key (airplanes.live) — add the key, or use another;
  * repeated invalid requests got the IP temporarily restricted (adsb.fi counts 4xx toward its limit).
On 403 a row backs off (60 s, then up to 10 min) instead of hammering the server, which would prolong a ban.

Every target is normalised to SI units:
  {icao, cs, lat, lon, altMsl [m]|None, altType "GEOM"|"BARO", gs [m/s], trk [deg], vs [m/s], cat, sq,
   reg, type, ground, src, age [s]}
Standard library only.
"""
import asyncio, json, math, ssl, time, urllib.error, urllib.parse, urllib.request

from . import __version__

FT = 0.3048
KT = 0.514444
FPM = 0.00508
NM = 1852.0
USER_AGENT = f"RC-IFD/{__version__} (RC Instrument Flight Display; +https://github.com/) Python-urllib"

BUILTIN = {
    "adsb.lol": "https://api.adsb.lol/v2/point/{lat}/{lon}/{dist}",
    "adsb.fi": "https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{dist}",
}
# known-problematic services, so the status line can say something useful
SERVICE_NOTES = {
    "adsb.one": "api.adsb.one blocks most API clients (server-side firewall) — use adsb.lol / adsb.fi",
    "airplanes.live": "airplanes.live requires prior approval or a feeder IP — add an API key or use another service",
}


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def fill_template(tpl, lat, lon, dist_km, key=""):
    """Substitute {lat} {lon} {dist} {dist_nm} {dist_km} {dist_m} {key} (case-insensitive)."""
    nm = max(1, min(250, int(round(dist_km * 1000 / NM))))
    vals = {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "dist": str(nm), "dist_nm": str(nm),
            "dist_km": str(max(1, int(round(dist_km)))), "dist_m": str(int(round(dist_km * 1000))),
            "key": urllib.parse.quote(key or "", safe="")}
    out, i = [], 0
    while i < len(tpl):
        if tpl[i] == "{":
            j = tpl.find("}", i)
            if j > i:
                name = tpl[i + 1:j].strip().lower()
                if name in vals:
                    out.append(vals[name]); i = j + 1; continue
        out.append(tpl[i]); i += 1
    return "".join(out)


def has_placeholders(tpl):
    t = tpl.lower()
    return "{lat}" in t and "{lon}" in t


def parse_aircraft_json(doc, src=""):
    """ADSBExchange v2 'ac' format (adsb.lol / adsb.fi v3 / adsb.one) and readsb / dump1090 'aircraft' format."""
    out = []
    lst = (doc.get("aircraft") or doc.get("ac") or []) if isinstance(doc, dict) else []
    now = time.monotonic()
    for a in lst:
        if not isinstance(a, dict):
            continue
        lat, lon = _num(a.get("lat")), _num(a.get("lon"))
        if lat is None or lon is None:
            lp = a.get("lastPosition") or {}
            lat, lon = _num(lp.get("lat")), _num(lp.get("lon"))
            seen = _num(lp.get("seen_pos"))
        else:
            seen = _num(a.get("seen_pos"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        if seen is not None and seen > 30:
            continue
        geom = _num(a.get("alt_geom"))
        baro = a.get("alt_baro", a.get("altitude"))
        on_ground = baro == "ground"
        baro = None if on_ground else _num(baro)
        alt, at = (geom * FT, "GEOM") if geom is not None else ((baro * FT, "BARO") if baro is not None else (None, ""))
        gs = _num(a.get("gs", a.get("speed")))
        vr = _num(a.get("geom_rate", a.get("baro_rate", a.get("vert_rate"))))
        icao = str(a.get("hex", "")).upper().lstrip("~")
        if not icao:
            continue
        out.append({"icao": icao, "cs": str(a.get("flight", "") or "").strip(), "reg": a.get("r", "") or "",
                    "type": a.get("t", "") or "", "lat": lat, "lon": lon, "altMsl": alt, "altType": at,
                    "ground": on_ground, "gs": gs * KT if gs is not None else None, "trk": _num(a.get("track")),
                    "vs": vr * FPM if vr is not None else 0.0, "cat": a.get("category", "") or "",
                    "sq": a.get("squawk", "") or "", "src": src, "_t": now - (seen or 0)})
    return out


def friendly_http(code, url, name):
    host = urllib.parse.urlparse(url).hostname or url
    for k, note in SERVICE_NOTES.items():
        if k in host:
            if code in (401, 403):
                return f"HTTP {code} — {note}"
    if code == 403:
        return (f"HTTP 403 from {host} — access refused (service blocks this client/IP, needs an API key, "
                f"or you were temporarily restricted)")
    if code == 401:
        return f"HTTP 401 from {host} — API key missing or wrong"
    if code == 404:
        return f"HTTP 404 from {host} — URL template wrong (check the path and placeholders)"
    if code == 429:
        return f"HTTP 429 from {host} — rate-limited"
    if code >= 500:
        return f"HTTP {code} from {host} — service error"
    return f"HTTP {code} from {host}"


class Provider:
    """One polled source (online service or local aircraft.json)."""

    def __init__(self, feed, name, tpl, key="", key_header="", builtin=False):
        self.feed, self.name, self.tpl = feed, name, tpl.strip()
        self.key, self.key_header = key or "", (key_header or "").strip()
        self.builtin = builtin
        self.task = None
        self.state, self.text = "idle", ""
        self.count = 0
        self.fails = 0
        self.last_ok = None
        self.last_url = ""
        self.last_query = None            # (lat, lon, radius) of the last successful online query

    @property
    def online(self):
        return has_placeholders(self.tpl)

    def info(self):
        age = None if self.last_ok is None else round(time.monotonic() - self.last_ok, 1)
        return {"name": self.name, "state": self.state, "text": self.text, "count": self.count, "age": age,
                "online": self.online}

    def _set(self, state, text):
        if (state, text) != (self.state, self.text):
            self.state, self.text = state, text
            self.feed._changed()

    def _fetch(self, url):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "identity"}
        if self.key and self.key_header:
            headers[self.key_header] = self.key
        req = urllib.request.Request(url, headers=headers)
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
            body = r.read(8_000_000)
        return json.loads(body.decode("utf-8", "replace"))

    async def run(self):
        loop = asyncio.get_running_loop()
        while True:
            wait = self.feed.interval if self.online else 1.0
            if self.online:
                pos = self.feed.center()
                if pos is None:
                    self._set("wait", "waiting for aircraft / home position")
                    await asyncio.sleep(1.0)
                    continue
                url = fill_template(self.tpl, pos[0], pos[1], self.feed.radius_km, self.key)
            else:
                url = self.tpl if "://" in self.tpl else "http://" + self.tpl
            self.last_url = url
            try:
                doc = await loop.run_in_executor(None, self._fetch, url)
                acs = parse_aircraft_json(doc, self.name)
                self.feed._merge(acs)
                self.count = len(acs)
                self.fails = 0
                self.last_ok = time.monotonic()
                n_all = len(doc.get("aircraft") or doc.get("ac") or []) if isinstance(doc, dict) else 0
                self._set("ok", f"OK — {n_all} aircraft" + (f" within {self.feed.radius_km:g} km" if self.online else " heard"))
            except asyncio.CancelledError:
                raise
            except urllib.error.HTTPError as e:
                self.fails += 1
                msg = friendly_http(e.code, url, self.name)
                if e.code == 429:
                    try:
                        ra = int(e.headers.get("Retry-After", "0"))
                    except (TypeError, ValueError):
                        ra = 0
                    wait = max(ra, 30 * min(self.fails, 4))
                    self._set("limited", f"{msg} — pausing {wait:.0f} s")
                elif e.code in (401, 403):
                    wait = min(600, 60 * (2 ** min(self.fails - 1, 3)))     # 60, 120, 240, 480 → max 600 s
                    self._set("blocked", f"{msg} — retry in {wait:.0f} s")
                elif e.code == 404:
                    wait = 120
                    self._set("error", f"{msg} — retry in {wait} s")
                else:
                    wait = min(60, 10 * self.fails)
                    self._set("error", f"{msg} — retry in {wait:.0f} s")
            except (urllib.error.URLError, OSError, ValueError) as e:
                self.fails += 1
                reason = getattr(e, "reason", None) or e
                text = str(reason)
                if "CERTIFICATE_VERIFY_FAILED" in text:
                    text = "TLS certificate check failed (macOS: run Install Certificates.command for your Python)"
                elif isinstance(e, ValueError):
                    text = "reply was not JSON (wrong URL?)"
                wait = min(30, (self.feed.interval if self.online else 1.0) * (1 + self.fails))
                self._set("error", f"unreachable: {text}"[:160])
            await asyncio.sleep(wait)


class AdsbFeed:
    def __init__(self, loop, on_update, on_status):
        self.loop, self.on_update, self.on_status = loop, on_update, on_status
        self.targets = {}
        self.providers = []
        self.sbs_task = None
        self.sbs_addr = ""
        self.sbs_state = ("idle", "")
        self.status = "off"
        self.cfg = {}
        self.radius_km = 25.0
        self.interval = 5.0
        self.pos = {}                     # craft / lkp / home positions from the display
        self.kind = "none"                # kept for server.py compatibility ("none" = feed idle)

    # ------------------------------------------------------------ position from the display
    def set_position(self, craft=None, lkp=None, home=None):
        for k, v in (("craft", craft), ("lkp", lkp), ("home", home)):
            if v and _num(v[0]) is not None and _num(v[1]) is not None:
                self.pos[k] = (float(v[0]), float(v[1]), time.monotonic())
            elif v is None and k == "craft":
                self.pos.pop("craft", None)

    def center(self):
        mode = self.cfg.get("center", "auto")
        order = {"auto": ("craft", "lkp", "home"), "home": ("home", "craft", "lkp"), "craft": ("craft", "lkp")}.get(mode, ("craft", "lkp", "home"))
        if mode == "fixed":
            lat, lon = _num(self.cfg.get("fixedLat")), _num(self.cfg.get("fixedLon"))
            return (lat, lon) if lat is not None and lon is not None else None
        for k in order:
            p = self.pos.get(k)
            if p:
                return p[0], p[1]
        return None

    # ------------------------------------------------------------ configuration
    async def configure(self, kind=None, addr=None, cfg=None):
        """cfg = {online:{"adsb.lol":bool,"adsb.fi":bool}, custom:[{name,url,key,keyHeader,on}], radiusKm, interval,
                  center:'auto'|'craft'|'home'|'fixed', fixedLat, fixedLon, local:{kind:'none'|'json'|'sbs', addr}}
           Old two-argument calls (kind, addr) still work: they configure the local receiver only."""
        if cfg is None:
            cfg = dict(self.cfg)
            cfg["local"] = {"kind": kind or "none", "addr": addr or ""}
        cfg = self._sanitize(cfg)
        if json.dumps(cfg, sort_keys=True) == json.dumps(self.cfg, sort_keys=True) and (self.providers or self.sbs_task or not self._wanted(cfg)):
            self.radius_km, self.interval = cfg["radiusKm"], cfg["interval"]
            return
        await self.stop()
        self.cfg = cfg
        self.radius_km, self.interval = cfg["radiusKm"], cfg["interval"]
        self.targets = {}
        for name, on in cfg["online"].items():
            if on and name in BUILTIN:
                self.providers.append(Provider(self, name, BUILTIN[name], builtin=True))
        for row in cfg["custom"]:
            if row.get("on") and row.get("url"):
                self.providers.append(Provider(self, row.get("name") or "custom", row["url"], row.get("key", ""), row.get("keyHeader", "")))
        lk = cfg["local"]
        if lk["kind"] == "json" and lk["addr"]:
            self.providers.append(Provider(self, "receiver", lk["addr"]))
        elif lk["kind"] == "sbs" and lk["addr"]:
            self.sbs_addr = lk["addr"]
            self.sbs_task = asyncio.ensure_future(self._run_sbs())
        for p in self.providers:
            p.task = asyncio.ensure_future(p.run())
        self.kind = "multi" if self._wanted(cfg) else "none"
        self._changed()

    @staticmethod
    def _wanted(cfg):
        return any(cfg["online"].values()) or any(r.get("on") and r.get("url") for r in cfg["custom"]) or cfg["local"]["kind"] != "none"

    @staticmethod
    def _sanitize(c):
        c = c or {}
        on = c.get("online") or {}
        custom = []
        for r in (c.get("custom") or [])[:8]:
            if isinstance(r, dict):
                custom.append({"name": str(r.get("name", "custom"))[:24], "url": str(r.get("url", "")).strip()[:400],
                               "key": str(r.get("key", ""))[:200], "keyHeader": str(r.get("keyHeader", "")).strip()[:64],
                               "on": bool(r.get("on"))})
        loc = c.get("local") or {}
        kind = loc.get("kind", "none") if loc.get("kind") in ("none", "json", "sbs") else "none"
        rad = _num(c.get("radiusKm")) or 25.0
        itv = _num(c.get("interval")) or 5.0
        return {"online": {k: bool(on.get(k)) for k in BUILTIN},
                "custom": custom,
                "radiusKm": max(2.0, min(460.0, rad)),          # 460 km ≈ the 250 nm API maximum
                "interval": max(2.0, min(60.0, itv)),
                "center": c.get("center") if c.get("center") in ("auto", "craft", "home", "fixed") else "auto",
                "fixedLat": _num(c.get("fixedLat")), "fixedLon": _num(c.get("fixedLon")),
                "local": {"kind": kind, "addr": str(loc.get("addr", "")).strip()[:300]}}

    async def stop(self):
        tasks = [p.task for p in self.providers if p.task] + ([self.sbs_task] if self.sbs_task else [])
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except BaseException:
                pass
        self.providers, self.sbs_task = [], None
        self.sbs_state = ("idle", "")
        self.kind = "none"

    # ------------------------------------------------------------ status / data
    def sources(self):
        out = [p.info() for p in self.providers]
        if self.sbs_task:
            out.append({"name": "SBS " + self.sbs_addr, "state": self.sbs_state[0], "text": self.sbs_state[1],
                        "count": sum(1 for a in self.targets.values() if a.get("src") == "SBS"), "age": None, "online": False})
        return out

    def _changed(self):
        src = self.sources()
        if not src:
            s = "off"
        else:
            ok = [x for x in src if x["state"] == "ok"]
            bad = [x for x in src if x["state"] in ("blocked", "error", "limited")]
            s = (f"{len(ok)}/{len(src)} sources OK" + (f" — {bad[0]['name']}: {bad[0]['text']}" if bad else "")) if len(src) > 1 else \
                f"{src[0]['name']}: {src[0]['text'] or src[0]['state']}"
        if s != self.status:
            self.status = s
            self.on_status(s)

    def _merge(self, acs):
        for a in acs:
            cur = self.targets.get(a["icao"])
            if cur is None or a["_t"] >= cur["_t"] - 0.5:
                if cur:
                    for k in ("cs", "reg", "type", "cat", "sq"):     # keep identity fields one feed may omit
                        if not a.get(k) and cur.get(k):
                            a[k] = cur[k]
                    if a.get("altMsl") is None and cur.get("altMsl") is not None:
                        a["altMsl"], a["altType"] = cur["altMsl"], cur["altType"]
                self.targets[a["icao"]] = a
        self.on_update()

    def snapshot(self, max_age=20):
        now = time.monotonic()
        out = []
        for k, a in list(self.targets.items()):
            age = now - a["_t"]
            if age > max_age:
                del self.targets[k]
                continue
            if a.get("lat") is None:
                continue
            out.append({k2: v for k2, v in a.items() if not k2.startswith("_")} | {"age": round(age, 1)})
        return out

    # ------------------------------------------------------------ SBS-1 BaseStation (port 30003)
    def parse_sbs_line(self, line):
        f = line.strip().split(",")
        if len(f) < 11 or f[0] != "MSG" or not f[4]:
            return
        icao = f[4].upper()
        a = self.targets.setdefault(icao, {"icao": icao, "cs": "", "lat": None, "lon": None, "altMsl": None, "altType": "BARO",
                                          "gs": None, "trk": None, "vs": 0.0, "cat": "", "sq": "", "src": "SBS", "_t": time.monotonic()})
        g = lambda i: f[i] if len(f) > i and f[i] != "" else None
        if g(10):
            a["cs"] = g(10).strip()
        if g(11) and _num(g(11)) is not None:
            a["altMsl"] = _num(g(11)) * FT
        if g(12) and _num(g(12)) is not None:
            a["gs"] = _num(g(12)) * KT
        if g(13) and _num(g(13)) is not None:
            a["trk"] = _num(g(13))
        if g(14) and g(15) and _num(g(14)) is not None:
            a["lat"], a["lon"] = _num(g(14)), _num(g(15))
            a["_t"] = time.monotonic()
        if g(16) and _num(g(16)) is not None:
            a["vs"] = _num(g(16)) * FPM
        if g(17):
            a["sq"] = g(17)
        if g(21):
            a["ground"] = g(21) in ("-1", "1")

    async def _run_sbs(self):
        host, _, port = self.sbs_addr.rpartition(":")
        host, port = (host or "localhost"), int(port or 30003)
        backoff = 1
        while True:
            try:
                self.sbs_state = ("wait", f"connecting {host}:{port}"); self._changed()
                r, w = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
                self.sbs_state = ("ok", f"OK — SBS {host}:{port}"); self._changed()
                backoff = 1
                last = time.monotonic()
                while True:
                    line = await asyncio.wait_for(r.readline(), 30)
                    if not line:
                        break
                    self.parse_sbs_line(line.decode("ascii", "replace"))
                    if time.monotonic() - last > 1:
                        last = time.monotonic()
                        self.on_update()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.sbs_state = ("error", f"SBS {host}:{port} — {e.__class__.__name__}; retry in {backoff}s"); self._changed()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10)

    # ------------------------------------------------------------ one-shot test for the Settings "Test" button
    async def test_url(self, tpl, key="", key_header=""):
        pos = self.center() or (self.pos.get("home") or (None, None))[:2]
        if has_placeholders(tpl) and (pos is None or pos[0] is None):
            return {"ok": False, "text": "No aircraft or home position yet — connect first (the Simulator works)."}
        url = fill_template(tpl, pos[0], pos[1], self.radius_km, key) if has_placeholders(tpl) else tpl
        p = Provider(self, "test", tpl, key, key_header)
        t0 = time.monotonic()
        try:
            doc = await self.loop.run_in_executor(None, p._fetch, url)
            acs = parse_aircraft_json(doc, "test")
            n_all = len(doc.get("aircraft") or doc.get("ac") or []) if isinstance(doc, dict) else 0
            return {"ok": True, "url": url, "ms": int((time.monotonic() - t0) * 1000),
                    "text": f"OK — {n_all} aircraft in reply, {len(acs)} with a usable position"}
        except urllib.error.HTTPError as e:
            return {"ok": False, "url": url, "text": friendly_http(e.code, url, "test")}
        except Exception as e:
            r = str(getattr(e, "reason", "") or e)
            if "CERTIFICATE_VERIFY_FAILED" in r:
                r = "TLS certificate check failed (macOS: run Install Certificates.command for your Python)"
            return {"ok": False, "url": url, "text": f"unreachable: {r}"[:200]}
