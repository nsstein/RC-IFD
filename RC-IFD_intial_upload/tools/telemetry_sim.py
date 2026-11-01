#!/usr/bin/env python3
"""
telemetry_sim.py — stand-in MAVLink aircraft for bench-testing the display without a flight controller.

  python tools/telemetry_sim.py --udp 127.0.0.1:14550          # send to the display's UDP link
  python tools/telemetry_sim.py --serial COM7 --baud 57600      # write to a serial port / USB-UART loopback
  python tools/telemetry_sim.py --pty                           # Linux/macOS: create a virtual serial port and print its path

Flies a 300 m circuit around a home point with climb-out, cruise and a slow battery drain, and answers
REQUEST_MESSAGE(HOME_POSITION) so the display's home logic is exercised.  Standard library only
(--serial needs pyserial).
"""
import argparse, math, os, socket, struct, sys, time

CRC = {0: 50, 1: 124, 24: 24, 30: 39, 33: 104, 74: 20, 147: 154, 168: 1, 242: 104, 253: 83}


def x25(data, extra):
    c = 0xFFFF
    for b in data + bytes([extra]):
        t = (b ^ (c & 0xFF)) & 0xFF
        t = (t ^ (t << 4)) & 0xFF
        c = ((c >> 8) ^ (t << 8) ^ (t << 3) ^ (t >> 4)) & 0xFFFF
    return c


class Mav:
    def __init__(self):
        self.seq = 0

    def frame(self, mid, payload):
        hdr = bytes([len(payload), 0, 0, self.seq & 255, 1, 1, mid & 255, (mid >> 8) & 255, (mid >> 16) & 255])
        self.seq += 1
        c = x25(hdr + payload, CRC[mid])
        return b"\xFD" + hdr + payload + struct.pack("<H", c)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--udp", metavar="HOST:PORT")
    g.add_argument("--serial", metavar="DEVICE")
    g.add_argument("--pty", action="store_true")
    ap.add_argument("--baud", type=int, default=57600)
    ap.add_argument("--home", default="35.7076,-81.2187")
    ap.add_argument("--rate", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=0, help="seconds (0 = forever)")
    a = ap.parse_args()
    hlat, hlon = map(float, a.home.split(","))

    send = recv = None
    if a.udp:
        h, p = a.udp.rsplit(":", 1)
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.setblocking(False)
        dst = (h, int(p))
        send = lambda b: sk.sendto(b, dst)

        def recv():
            try:
                return sk.recv(4096)
            except (BlockingIOError, OSError):
                return b""
    elif a.serial:
        import serial
        ser = serial.Serial(a.serial, a.baud, timeout=0)
        send, recv = ser.write, lambda: ser.read(4096)
    else:
        import pty, tty
        m, s = pty.openpty()
        tty.setraw(s)
        os.set_blocking(m, False)
        print("virtual serial port:", os.ttyname(s), flush=True)
        send = lambda b: os.write(m, b)

        def recv():
            try:
                return os.read(m, 4096)
            except (BlockingIOError, OSError):
                return b""

    mav = Mav()
    t0 = time.time()
    k = 0
    mah = 0.0
    home_req = False
    send(mav.frame(253, struct.pack("<B50s", 6, b"telemetry_sim: ArduPlane stand-in")))
    while True:
        t = time.time() - t0
        if a.duration and t > a.duration:
            break
        armed = t > 3
        alt = 0 if not armed else min(80, (t - 3) * 4)
        ang = max(0.0, t - 8) * 0.06
        r = 300 if t > 8 else 0
        lat = hlat + (r * math.cos(ang)) / 111320
        lon = hlon + (r * math.sin(ang)) / (111320 * math.cos(math.radians(hlat)))
        hdg = (math.degrees(ang) + 90) % 360 if t > 8 else 40
        roll = math.radians(25) if t > 8 else 0
        pitch = math.radians(8 if alt < 80 and armed else 1)
        spd = 20 if armed else 0
        curr = 25 if armed else 0.5
        mah += curr / 3.6 / a.rate
        vbat = 16.6 - mah / 3000 * 2.6
        tb = int(t * 1000)
        out = [mav.frame(30, struct.pack("<Iffffff", tb, roll, pitch, math.radians(((hdg + 180) % 360) - 180), 0, 0, 0)),
               mav.frame(74, struct.pack("<ffffhH", spd, spd + 2, alt + 300, 4 if alt < 80 and armed else 0, int(hdg), 60 if armed else 0))]
        if k % 2 == 0:
            out.append(mav.frame(33, struct.pack("<IiiiihhhH", tb, int(lat * 1e7), int(lon * 1e7), int((alt + 300) * 1000), int(alt * 1000), 0, 0, 0, int(hdg * 100))))
            out.append(mav.frame(24, struct.pack("<QiiiHHHHBB", int(time.time() * 1e6), int(lat * 1e7), int(lon * 1e7), int((alt + 300) * 1000), 90, 120, int(spd * 100), int(hdg * 100), 3, 13)))
        if k % 5 == 0:
            out.append(mav.frame(1, struct.pack("<IIIHHhHHHHHHb", 0x20, 0x20, 0x20, 300, int(vbat * 1000), int(curr * 100), 0, 0, 0, 0, 0, 0, int(100 - mah / 30))))
            out.append(mav.frame(147, struct.pack("<iih10HhBBBb", int(mah), -1, 32767, int(vbat * 1000), *([65535] * 9), int(curr * 100), 0, 0, 0, int(100 - mah / 30))))
            out.append(mav.frame(168, struct.pack("<fff", 250, 5, 0)))
        if k % 10 == 0:
            out.append(mav.frame(0, struct.pack("<IBBBBB", 5 if armed else 0, 1, 3, 81 | (128 if armed else 0), 4, 3)))
        rx = recv() or b""
        if b"\xFD" in rx or b"\xFE" in rx:
            home_req = home_req or (bytes([76]) in rx)
        if home_req:
            home_req = False
            out.append(mav.frame(242, struct.pack("<iii3f4f3f", int(hlat * 1e7), int(hlon * 1e7), 300000, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0)))
        send(b"".join(out))
        k += 1
        time.sleep(1 / a.rate)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
