# Mock ADS-B aggregators on localhost: records each request URL + UA.
import http.server, json, threading, time, sys, re
LOG=[]
def ac(lat,lon):
    return [{"hex":"a4f2c1","flight":"N4021K  ","alt_baro":1450,"alt_geom":1500,"gs":95,"track":180,"geom_rate":-256,"lat":lat+0.008,"lon":lon,"seen_pos":0.3,"category":"A1","r":"N4021K","t":"C172"},
            {"hex":"ab12cd","flight":"UAL233","alt_baro":11000,"gs":310,"track":45,"baro_rate":1200,"lat":lat+0.2,"lon":lon+0.2,"seen_pos":1.0},
            {"hex":"~2b0c11","alt_baro":"ground","gs":0,"lat":lat+0.001,"lon":lon,"seen_pos":0.5}]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        ua=self.headers.get("User-Agent",""); LOG.append((self.path,ua,self.headers.get("api-auth","")))
        m=re.search(r"(-?\d+\.\d+)/(?:lon/)?(-?\d+\.\d+)/(?:dist/)?(\d+)",self.path)
        def send(code,obj):
            b=json.dumps(obj).encode() if obj is not None else b"blocked"; self.send_response(code)
            self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        if self.path.startswith("/lol/"):                       # adsb.lol-like: rejects library-default UA
            if ua.startswith("Python-urllib") and "RC-IFD" not in ua: return send(403,None)
            return send(200,{"ac":ac(float(m[1]),float(m[2])),"msg":"No error","total":3})
        if self.path.startswith("/fi/"):                        # adsb.fi v3-like
            return send(200,{"ac":ac(float(m[1]),float(m[2]))[:1],"resultCount":1})
        if self.path.startswith("/one/"):                       # adsb.one-like: Cloudflare 403 always
            return send(403,None)
        if self.path.startswith("/keyed/"):
            return send(200,{"ac":ac(float(m[1]),float(m[2]))[1:2]}) if self.headers.get("api-auth")=="SECRET" else send(401,None)
        if self.path.startswith("/rx/aircraft.json"):           # local readsb
            return send(200,{"now":time.time(),"aircraft":[{"hex":"c0ffee","flight":"LOCAL1","alt_geom":900,"gs":80,"track":90,"lat":35.71,"lon":-81.21,"seen_pos":0.2}]})
        send(404,None)
srv=http.server.ThreadingHTTPServer(("127.0.0.1",18555),H)
threading.Thread(target=srv.serve_forever,daemon=True).start()
