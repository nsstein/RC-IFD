import sys, asyncio, time; import os; H=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,os.path.dirname(H)); sys.path.insert(0,H)
import adsb_mock_services as mock
from ifd_backend import adsb
R=[]; ok=lambda c,m:R.append(("PASS " if c else "FAIL ")+m)
# placeholders
u=adsb.fill_template("https://x/v2/point/{lat}/{lon}/{dist}",35.707,-81.219,25)
ok(u=="https://x/v2/point/35.707000/-81.219000/13","nm conversion: "+u)
ok(adsb.fill_template("{LAT},{Lon},{dist_km},{dist_m},{key}",1,2,10,"a b")=="1.000000,2.000000,10,10000,a%20b","all placeholders + case-insensitive")
ok(adsb.fill_template("x/{dist}",0,0,1000).endswith("/250"),"radius capped at 250 nm")
ok(adsb.fill_template("x/{unknown}/{lat}",1,2,10)=="x/{unknown}/1.000000","unknown placeholder left alone")
async def main():
  loop=asyncio.get_running_loop(); st=[]
  f=adsb.AdsbFeed(loop,lambda:None,lambda s:st.append(s))
  base="http://127.0.0.1:18555"
  # monkeypatch built-ins to mock
  adsb.BUILTIN["adsb.lol"]=base+"/lol/v2/point/{lat}/{lon}/{dist}"; adsb.BUILTIN["adsb.fi"]=base+"/fi/api/v3/lat/{lat}/lon/{lon}/dist/{dist}"
  await f.configure(cfg={"online":{"adsb.lol":True,"adsb.fi":True},"radiusKm":25,"interval":2,
     "custom":[{"name":"adsb.one","url":base+"/one/v2/point/{lat}/{lon}/{dist}","on":True},
               {"name":"keyed","url":base+"/keyed/{lat}/{lon}/{dist}","key":"SECRET","keyHeader":"api-auth","on":True}],
     "local":{"kind":"json","addr":base+"/rx/aircraft.json"}})
  await asyncio.sleep(1.5)
  src={s["name"]:s for s in f.sources()}
  ok(src["adsb.lol"]["state"]=="wait","online waits for a position before querying")
  ok(src["receiver"]["state"]=="ok","local receiver runs without a position")
  f.set_position(craft=[35.707,-81.219],home=[35.70,-81.21])
  await asyncio.sleep(3.0)
  src={s["name"]:s for s in f.sources()}
  for n in ("adsb.lol","adsb.fi","keyed","receiver"): ok(src[n]["state"]=="ok",f"{n}: {src[n]['text']}")
  ok(src["adsb.one"]["state"]=="blocked" and "403" in src["adsb.one"]["text"],"adsb.one 403 explained + backoff: "+src["adsb.one"]["text"])
  snap={a["icao"]:a for a in f.snapshot()}
  ok("A4F2C1" in snap and "AB12CD" in snap and "C0FFEE" in snap,"merged sources "+",".join(sorted(snap)))
  ok(sum(1 for a in f.snapshot() if a["icao"]=="A4F2C1")==1,"de-duplicated by ICAO across lol+fi")
  ok(snap["2B0C11"]["ground"] if "2B0C11" in snap else True,"ground flag parsed")
  lolreq=[l for l in mock.LOG if l[0].startswith("/lol/")]
  ok(lolreq and "35.707000/-81.219000/13" in lolreq[-1][0],"lol URL used aircraft lat/lon + 13 nm: "+lolreq[-1][0])
  ok(lolreq[-1][1].startswith("RC-IFD/"),"descriptive User-Agent sent: "+lolreq[-1][1][:40])
  # centre fallback: craft lost -> LKP
  f.set_position(craft=None,lkp=[35.8,-81.3]); ok(f.center()==(35.8,-81.3),"centre falls back to last known position")
  f.cfg["center"]="home"; ok(f.center()==(35.70,-81.21),"centre = home when selected")
  n1=len(lolreq); await asyncio.sleep(2.3); ok(len([l for l in mock.LOG if l[0].startswith('/lol/')])-n1<=2,"poll interval respected")
  one=len([l for l in mock.LOG if l[0].startswith('/one/')]); ok(one==1,f"blocked service not hammered ({one} request)")
  t=await f.test_url(base+"/one/v2/point/{lat}/{lon}/{dist}"); ok(not t["ok"] and "403" in t["text"],"Test button: "+t["text"][:60])
  t=await f.test_url(base+"/fi/api/v3/lat/{lat}/lon/{lon}/dist/{dist}"); ok(t["ok"],"Test button OK: "+t["text"])
  t=await f.test_url(base+"/nothere/{lat}/{lon}"); ok(not t["ok"] and "404" in t["text"],"404 explained")
  # legacy (kind, addr) call still works
  await f.configure("json",base+"/rx/aircraft.json"); ok(any(s["name"]=="receiver" for s in f.sources()),"legacy configure(kind,addr)")
  await f.stop()
asyncio.run(main())
print("\n".join(R)); print(sum(r.startswith("PASS") for r in R),"/",len(R))
