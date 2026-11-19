# Quick reference card

Keep this page for field use. Full explanations are in the [User Guide](USER_GUIDE.md).

## Before flight

- [ ] Connected — status bar `SRC` and `LINK` green
- [ ] GPS 3D fix with 6+ satellites
- [ ] `HOME SOURCE` shows `FC` or `ARM POINT`
- [ ] Battery cells, capacity and reserve set for this pack
- [ ] Bench check done for this aircraft
- [ ] `● REC` showing (auto-log)
- [ ] `TRAFFIC` tab: source `OK` (if used)

Bench check: lift nose → horizon moves **down**. Roll right → horizon tilts **left**. Fix with Settings → Invert pitch / roll if needed.

## If video is lost

1. Wings level; stop climb/dive.
2. Read RTH panel: `OK` / `MARGINAL` / `CANNOT REACH HOME`.
3. Turn as shown until `HOME AHEAD — HOLD`.
4. Keep the aircraft symbol on the magenta steering bars.
5. Correct for wind with the green track diamond.
6. Stay above the cyan RTH line in the vertical profile.
7. `OVERHEAD HOME` — circle gently and look for the aircraft.

**Down?** Tap the `CRAFT` / `LKP` row to copy coordinates. Use `CRAFT FROM HOME` for bearing and distance to walk.

## Colors and recovery

| Color / flag | Meaning |
| --- | --- |
| Magenta | Home / guidance — steer towards it |
| Green | Normal |
| Cyan | Values you set |
| Amber | Caution |
| Red | Warning |
| `ATT SPD ALT V/S HDG` (boxed red) | No valid data — do not use that instrument |
| `LINK LOST` | Fly last heading; watch `LOS` timer |

| Situation | Recovery |
| --- | --- |
| Bank > 60° | Roll level first |
| Nose low | Roll level, then pull |
| Nose high | Lower the nose |
| `TERRAIN` / `SINK RATE` | Pull up |

## Keys and buttons

| Control | Action |
| --- | --- |
| Space | Replay play / pause |
| Moon button | Night palette |
| F11 (Windows/Linux), Ctrl+Cmd+F (macOS) | Full screen; Esc exits |
| ⚙ button | Settings; Esc closes |
| `M` (simulator) | Toggle waypoint mission / free flight |
| ← / → on a tab | Switch ALERTS / MISSION / TRAFFIC |

## Baud rates and logs

| Protocol | Baud |
| --- | --- |
| MAVLink | 57600 / 115200 |
| CRSF | 420000 (try 416666) |
| MSP | 115200 |

| Platform | Logs folder |
| --- | --- |
| Windows | `%LOCALAPPDATA%\RC-IFD\logs` |
| macOS | `~/Library/Application Support/RC-IFD/logs` |
| Linux / Pi | `~/.local/share/rc-ifd/logs` |

> Receive-only ADS-B: manned aircraft cannot see you. See and avoid, and give way. RC-IFD is a pilot aid, not certified avionics.
