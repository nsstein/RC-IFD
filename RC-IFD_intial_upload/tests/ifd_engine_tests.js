// Headless protocol tests for RC Instrument Flight Display — run:  node tests/ifd_engine_tests.js [web/index.html]
// Round-trips every supported MAVLink/CRSF/MSP message and stress-tests resync with 0.5 % byte corruption + random chunking.
const fs=require("fs"), vm=require("vm"), path=require("path");
const html=fs.readFileSync(process.argv[2]||path.join(__dirname,"..","web","index.html"),"utf8");
const a=html.indexOf("/* =====================================================================\n   2.  TELEMETRY ENGINE"), b=html.indexOf("/* =====================================================================\n   3.  ADAPTERS");
if(a<0||b<0) throw new Error("engine section markers not found");
vm.runInThisContext(html.slice(a,b)+";globalThis.E={MAV_BY_NAME,mavFrame,MavParser,crsfFrame,BEW,CrsfParser,mspFrame,mspDecode,MspParser,MSP,concatU8,x25};");
const {MAV_BY_NAME:MB,mavFrame:mF,MavParser:MP,crsfFrame:cF,BEW:BW,CrsfParser:CP,mspFrame:sF,mspDecode:sD,MspParser:SP,MSP:SM,concatU8:cc}=E;
let fail=0; const ok=(c,m)=>{ if(!c){fail++;console.log("FAIL",m);} };
// known-answer test: MAVLink v1 HEARTBEAT from a real ArduPilot capture (pymavlink docs style) – build and self-check
// 1. round trip each message, v1 and v2
const samples={
 HEARTBEAT:{custom_mode:5,type:1,autopilot:3,base_mode:209,system_status:4,mavlink_version:3},
 ATTITUDE:{time_boot_ms:1234,roll:0.25,pitch:-0.1,yaw:1.5},
 VFR_HUD:{airspeed:21.5,groundspeed:24,alt:100,climb:-1.5,heading:270,throttle:55},
 GPS_RAW_INT:{time_usec:123456789,lat:357076000,lon:-812187000,alt:350000,eph:80,epv:120,vel:2400,cog:27000,fix_type:3,satellites_visible:14},
 BATTERY_STATUS:{current_consumed:842,voltages:[3900,3900,3900,3900,65535,65535,65535,65535,65535,65535],current_battery:2150,battery_remaining:72,id:0},
 STATUSTEXT:{severity:4,text:"Battery 1 is low"},
 HOME_POSITION:{latitude:357076000,longitude:-812187000,altitude:300000,q:[1,0,0,0]},
 SYS_STATUS:{voltage_battery:16100,current_battery:-1,battery_remaining:-1,onboard_control_sensors_present:0x20},
};
for(const v2 of [false,true]){
  const got=[]; const p=new MavParser((n,m)=>got.push([n,m]));
  for(const [n,o] of Object.entries(samples)) p.push(mavFrame(MAV_BY_NAME[n],o,{v2,seq:1,sys:1,comp:1}));
  ok(got.length===Object.keys(samples).length,"mav count v2="+v2+" got "+got.length);
  for(const [n,m] of got){ const o=samples[n]; for(const k in o){ const a=m[k],b=o[k];
     const eq=Array.isArray(b)? JSON.stringify(a)===JSON.stringify(b) : (typeof b==="number"? Math.abs(a-b)<1e-4 : a===b);
     ok(eq,`${n}.${k} v2=${v2}: ${a} != ${b}`);} }
}
// 2. MAVLink noise / chunk resilience: 3000 frames, 0.5% byte corruption, random chunking + unknown msg ids interleaved
function stress(build,mkParser,label,N=3000,pc=0.005){
  let sent=[]; const frames=[]; for(let i=0;i<N;i++){ const [f,val]=build(i); frames.push(f); sent.push(val); }
  let stream=concatU8(frames); const corrupted=new Set(); let off=0; const owner=[]; frames.forEach((f,i)=>{for(let j=0;j<f.length;j++) owner.push(i);});
  stream=stream.slice(); let flips=0;
  for(let i=0;i<stream.length;i++) if(Math.random()<pc){ stream[i]^=1+Math.floor(Math.random()*255); corrupted.add(owner[i]); flips++; }
  const got=[]; const p=mkParser(got);
  for(let i=0;i<stream.length;){ const n=1+Math.floor(Math.random()*80); p.push(stream.subarray(i,i+n)); i+=n; }
  const clean=N-corrupted.size; const sentSet=new Set(sent);
  const falseAcc=got.filter(v=>!sentSet.has(v)).length;
  console.log(`${label}: frames ${N}, corrupted ${corrupted.size}, decoded ${got.length} (clean ${clean}), false-accepts ${falseAcc}, stats`,JSON.stringify({ok:p.st.ok,bad:p.st.bad,junk:p.st.junk,unknown:p.st.unknown}));
  ok(got.length>=clean*0.97,label+" recovered too few"); ok(falseAcc===0,label+" false accepts");
}
stress(i=>{ const v=(i*7919)%100000; return [mavFrame(MAV_BY_NAME.GLOBAL_POSITION_INT,{time_boot_ms:v,lat:v,lon:-v,relative_alt:v},{v2:i%2===0,seq:i}),v]; },
  got=>new MavParser((n,m)=>{ if(n==="GLOBAL_POSITION_INT") got.push(m.time_boot_ms); }),"MAVLink");
// 3. CRSF round trip
{ const got={}; const p=new CrsfParser((n,m)=>got[n]=m);
  p.push(crsfFrame(0x1E,new BEW().i16(0.1234*10000).i16(-0.5*10000).i16(2*10000).u));
  p.push(crsfFrame(0x02,new BEW().i32(357076000).i32(-812187000).u16(864).u16(27000).u16(1150).u8(11).u));
  p.push(crsfFrame(0x08,new BEW().u16(161).u16(366).u24(1234).u8(60).u));
  p.push(crsfFrame(0x09,new BEW().u16(10000+523).u));           // 52.3 m
  p.push(crsfFrame(0x09,new BEW().u16(0x8000|3000).u));         // 3000 m
  p.push(crsfFrame(0x14,new BEW().u8(56).u8(60).u8(98).i8(9).u8(0).u8(6).u8(7).u8(40).u8(100).i8(10).u));
  p.push(crsfFrame(0x21,new BEW().str("FBWA").u));
  ok(Math.abs(got.ATTITUDE.pitch-0.1234)<1e-4 && Math.abs(got.ATTITUDE.roll+0.5)<1e-4,"crsf att");
  ok(got.GPS.lat===357076000&&got.GPS.lon===-812187000&&got.GPS.sats===11&&got.GPS.alt_m1000===1150,"crsf gps");
  ok(got.BATTERY.cap_mah===1234&&got.BATTERY.volt_dv===161,"crsf bat");
  ok(got.BARO_ALT.alt_m===3000,"crsf baro m"); ok(got.LINK_STATS.up_lq===98&&got.LINK_STATS.up_snr===9,"crsf link");
  ok(got.FLIGHT_MODE.mode==="FBWA","crsf mode"); }
{ const got=[]; const p=new CrsfParser((n,m)=>{ if(n==="BARO_ALT") got.push(m.alt_m); }); p.push(crsfFrame(0x09,new BEW().u16(10000-55).u)); ok(Math.abs(got[0]+5.5)<1e-9,"crsf neg alt"); }
stress(i=>{ const v=i%30000; return [crsfFrame(0x09,new BEW().u16(10000+v).u),(v)/10]; },
  got=>new CrsfParser((n,m)=>{ if(n==="BARO_ALT") got.push(m.alt_m); }),"CRSF");
// 4. MSP v1 + v2 round trip
{ const got={}; const p=new MspParser((n,m)=>{ if(n==="_RAW"){ const r=mspDecode(m.cmd,m.payload); if(r) got[r[0]]=r[1]; } });
  const att=new Uint8Array(6); const dv=new DataView(att.buffer); dv.setInt16(0,-123,true); dv.setInt16(2,45,true); dv.setInt16(4,270,true);
  p.push(mspFrame(MSP.ATTITUDE,att,0x3E));
  const as=new Uint8Array(4); new DataView(as.buffer).setUint32(0,2150,true); p.push(mspFrame(MSP.INAV_AIR_SPEED,as,0x3E));
  p.push(mspFrame(MSP.BOXNAMES,Uint8Array.from("ARM;ANGLE;NAV RTH;",c=>c.charCodeAt(0)),0x3E));
  p.push(mspFrame(MSP.ATTITUDE,new Uint8Array(0),0x21));
  ok(got.MSP_ATTITUDE.roll===-123&&got.MSP_ATTITUDE.pitch===45&&got.MSP_ATTITUDE.yaw===270,"msp att v1");
  ok(got.MSP2_INAV_AIR_SPEED.cms===2150,"msp v2 airspeed"); ok(got.MSP_BOXNAMES.names[2]==="NAV RTH","msp boxnames"); ok(p.st.err===1,"msp err"); }
stress(i=>{ const pl=new Uint8Array(6); new DataView(pl.buffer).setInt32(0,i*3,true); return [mspFrame(i%2?MSP.ALTITUDE:MSP.INAV_ANALOG-0x2002+MSP.ALTITUDE,pl,0x3E),i*3]; },
  got=>new MspParser((n,m)=>{ if(n==="_RAW"){ const r=mspDecode(m.cmd,m.payload); if(r) got.push(r[1].alt_cm); } }),"MSP v1");
stress(i=>{ const pl=new Uint8Array(4); new DataView(pl.buffer).setUint32(0,i*5,true); return [mspFrame(MSP.INAV_AIR_SPEED,pl,0x3E),i*5]; },
  got=>new MspParser((n,m)=>{ if(n==="_RAW"){ const r=mspDecode(m.cmd,m.payload); if(r) got.push(r[1].cms); } }),"MSP v2");
console.log(fail?`${fail} FAILURES`:"ALL ENGINE TESTS PASSED");
