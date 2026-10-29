<img width="1797" height="1024" alt="Screenshot 2026-10-29 at 5 16 08 PM" src="https://github.com/user-attachments/assets/50a47b69-09d1-4599-9149-335088a18a59" />

RC Instrument Flight Display
User Guide
Version 2.0  ·  Windows · macOS · Linux · Raspberry Pi  ·  MAVLink · CRSF · MSP
 
An instrument cluster for RC aircraft that lets you fly the aircraft on instruments via telemetry feed. It reads live telemetry, draws instruments, logs every flight and can replay it afterwards.

 Contents
1. About the application   Key capabilities, colour conventions
2. What you need   Computers, telemetry sources, baud rates
3. Installing   macOS, Windows, Linux, Raspberry Pi, start-up options
4. Connecting to the aircraft   Connection settings, first connection, bench check
5. The screen at a glance   Flight display, navigation and profile, return-to-home, systems, alerts
6. Flying home on instruments   Step-by-step procedure
7. Recovery cues and failure indications   Unusual attitude, lost data
8. Flight logs and replay   Auto-logging, log locations, replay controls
9. Settings reference   Every setting and its default
10. Troubleshooting   Common problems and fixes
11. Sharing the application   What to send to each type of computer
Quick reference card   One page for the field
1. About the application
The display turns the aircraft's telemetry into a full instrument panel modelled on real cockpit displays (Boeing 787 primary flight and navigation displays, Garmin G1000, Airbus warning system).
Key capabilities
•	Artificial horizon with speed and altitude tapes, vertical speed, heading tape and magenta steering bars that point you home.
•	Moving map with home pointer, glide-range ring and breadcrumb trail, plus a side-view profile of the aircraft above the ground.
•	Return-to-home panel with a large direction arrow, turn instruction, distance, time to home and a battery check that says whether you can make it.
•	Battery, current, throttle, link quality and GPS gauges, and a colour-coded alert list with Master Warning / Master Caution.
•	Works with MAVLink (ArduPilot, PX4), CRSF (ExpressLRS, Crossfire) and MSP (INAV, Betaflight) over USB/serial, UDP or TCP.
•	Automatic flight logging to the computer, with replay, seek and a standard .tlog for Mission Planner.
Colour conventions
The same colours mean the same thing everywhere on the screen, as in a real cockpit:
Colour	Meaning
White	Scales and current values
Green	Normal / active / within limits
Magenta	Guidance and home — steer towards magenta
Cyan	Values you have set (return altitude, wind, trail)
Amber	Caution — needs attention soon
Red	Warning or failed data. A red boxed label (ATT, SPD, ALT…) means that instrument has no valid data — do not use it

Important: This is a pilot aid, not a certified avionics system. Always follow your local regulations (including visual line-of-sight rules), configure your flight controller's own failsafe and return-to-home, and he display should not be treated as a back-up.

2. What you need
Item	Requirement
Computer	Windows 10/11, macOS 12 or later (Apple Silicon or Intel build to match), Linux desktop, or Raspberry Pi 4/5 with Raspberry Pi OS (64-bit recommended)
Telemetry source	A flight controller or radio sending MAVLink, CRSF or MSP telemetry, connected by USB cable, USB-to-serial adapter, the Pi's GPIO UART, or a Wi-Fi bridge (UDP/TCP)
Screen (field use)	A bright, sunlight-readable display. 7–10 inch works well on a Raspberry Pi
Power (field use)	A stable 5 V / 3 A or better supply for a Pi — brown-outs during radio transmit cause resets
Software	Packaged app: nothing else. Running from the source folder: Python 3.9+ (the launch scripts install pyserial and pywebview)

Recommended telemetry settings
Protocol	Typical source	Baud	Notes
MAVLink	ArduPilot / PX4 telemetry port or USB	57600 / 115200	Match the flight controller's SERIALx_BAUD. USB ignores baud
CRSF	ExpressLRS / Crossfire receiver telemetry	420000	Try 416666 if the adapter rejects 420000
MSP	INAV / Betaflight USB or UART with MSP enabled	115200	The app polls the flight controller; the port must be set to MSP

3. Installing
macOS (packaged app)
1.	Open RC-IFD.dmg and drag RC-IFD into Applications (or copy RC-IFD.app there directly).
2.	First launch only: right-click RC-IFD in Applications, choose Open, then Open again. macOS asks this because the app is not signed with a paid Apple developer certificate.
3.	If macOS says the app is damaged, run this once in Terminal, then open it again:
xattr -dr com.apple.quarantine /Applications/RC-IFD.app
Note: A build made on an Apple Silicon Mac only runs on Apple Silicon Macs, and an Intel build only on Intel Macs. Ask for the build that matches your Mac (Apple menu → About This Mac).

Windows
1.	Install Python 3.9 or later from python.org and tick Add python.exe to PATH.
2.	Unzip the RC-IFD folder somewhere permanent (for example Documents\RC-IFD).
3.	Double-click run_windows.bat. The first run sets itself up (about a minute); later runs open straight away.
4.	Windows normally installs USB serial drivers automatically. For CH340 adapters you may need the driver from the WCH website.
Linux desktop
cd RC-IFD
./run_mac_linux.sh
sudo usermod -aG dialout $USER        # once, then log out and back in
sudo cp deploy/linux/99-rc-ifd.rules /etc/udev/rules.d/   # once: serial access + stops ModemManager interfering
Raspberry Pi (field unit)
bash deploy/raspberrypi/install_pi.sh
This installs everything, starts the telemetry and logging service at boot (it logs even with no screen attached), and opens the display full-screen at login. Reboot once afterwards. Press Alt+F4 to leave full-screen mode. To use the Pi's own UART pins (GPIO 14/15), turn off the serial console in raspi-config → Interface Options → Serial Port and choose /dev/serial0 as the port.
Start-up options
Option	Effect
--fullscreen	Start full-screen
--kiosk	Full-screen with no window controls (field use / Raspberry Pi)
--lan	Let phones and tablets on the same Wi-Fi view the display at http://<computer-ip>:8765 (view only)
--headless	Run telemetry and logging only, with no window
--data-dir <path>	Store settings and logs somewhere else, e.g. a USB drive

4. Connecting to the aircraft
 
Figure 1 – Settings → Connection
#	Item	What it tells you / how to use it
1	Protocol	MAVLink, CRSF or MSP — must match what the aircraft sends
2	Transport	Serial port (USB/UART), UDP, TCP, Simulator or Replay
3	Serial port + ⟳	Ports are listed with their device name and USB ID. Press ⟳ after plugging something in
4	Baud rate	See the table in section 2. Changing it reconnects automatically
5	Hold DTR/RTS low	Leave ticked. Stops ESP32/Arduino-based receivers and bridges from resetting when the port opens
6	Connect / Disconnect	Opens or closes the link. The app reconnects by itself if the cable is unplugged and plugged back in
7	Log now / Stop log	Manual logging control. Normally not needed — logging starts automatically
8	Set home here	Makes the aircraft's current position the home point (useful if home was set in the wrong place)
9	Status and log	Green = connected and receiving. The lines below show what the app detected (vehicle type, flight logs folder, errors)

First connection
1.	Power the aircraft and connect the telemetry source to the computer.
2.	Click ⚙ (top right) to open Settings.
3.	Choose the Protocol and Transport, then the Serial port and Baud rate (or the UDP port / TCP address).
4.	Click Connect. The status line turns green and the instruments come alive within a few seconds.
5.	Do the bench check below before the first flight with each aircraft.
Bench check (do this once per aircraft)
1.	Lift the aircraft's nose: the horizon line on the display must move down.
2.	Roll the aircraft to the right: the horizon must tilt left (anticlockwise).
3.	If either is wrong, tick Invert pitch or Invert roll under Protocol options. This is saved separately for each protocol.
4.	Check that the HOME SOURCE in the return-home panel shows FC (home from the flight controller) or ARM POINT before take-off.
Tip: With UDP, the app listens on the port you enter (14550 by default) — point MAVProxy, mavlink-router or your Wi-Fi telemetry bridge at the computer's IP address and that port. With TCP, enter host:port, for example 192.168.4.1:5760 for most ESP32 bridges.

5. The screen at a glance
 
Figure 2 – Main display
#	Item	What it tells you / how to use it
1	Status bar	Telemetry source, link quality, flight mode, armed state, GPS fix and satellites, speed source and ● REC when logging
2	Primary flight display	Attitude, speed, altitude, climb/sink and heading — your main instrument
3	Navigation display	Map-style compass with home, trail and glide range, plus the side-view profile
4	Return-to-home panel	Which way and how far to home, and whether the battery will get you there
5	Systems	Battery, current, throttle, link and GPS health
6	Alerts	Warnings (red), cautions (amber) and messages, with flight time and link-loss timers
7	Master Warning / Caution	Flash when a new warning or caution appears. Tap to acknowledge
8	Night mode, full screen, settings	☾ switches to a red night palette; ⛶ full screen; ⚙ opens Settings

Tip: The top-bar labels turn amber or red when something is degraded. A red LINK LOST or GPS LOST label is the first thing to look for if the display seems frozen.

5.1 Primary flight display
 
Figure 3 – Primary flight display
#	Item	What it tells you / how to use it
1	Mode strip	Flight mode, armed state, speed source (AIR = airspeed sensor, GPS GS = ground speed) and FD: HOME when steering guidance is active
2	Speed tape	Current speed in the box. Red band = below stall speed, amber = close to stall, red/white = above never-exceed speed. Green line = speeding up or slowing down
3	Bank scale	Bank angle in degrees. The pointer turns amber beyond the bank limit (55° by default)
4	Aircraft symbol	The aircraft's wings. Where it sits against the horizon is the aircraft's attitude
5	Flight-path marker	Green circle with wings: where the aircraft is actually going, including wind drift and climb or sink. If it sits below the horizon you are descending, whatever the nose is doing
6	Magenta steering bars	Fly the yellow aircraft symbol onto the cross of the bars — the vertical bar steers you towards home, the horizontal bar towards the return altitude
7	Altitude tape	Height above home. Magenta marker = return altitude; amber strip = low-altitude zone; hatched = below home level
8	Vertical speed	Climb (up) or sink (down) rate. Turns amber then red for steep descents
9	Heading tape	Compass heading in the box. Green diamond = actual track over the ground; magenta marker = direction of home (◀ HOME / HOME ▶ when off-scale)
10	Ground speed and wind	GS and wind direction/speed with an arrow showing which way it blows. EST means the wind is estimated by the app

5.2 Navigation display and vertical profile
 
Figure 4 – Navigation display with vertical profile
#	Item	What it tells you / how to use it
1	Speeds and wind	Ground speed, airspeed and wind
2	Home data	Distance and compass bearing to home
3	Heading	Current heading (or N-UP in north-up mode)
4	Home	Magenta H and line to home, with a magenta pointer on the compass ring showing home's direction even when it is off the map
5	Glide ring	Amber dashed circle: how far the aircraft could glide with the motor stopped, from its present height
6	Track line	Green dashed line: the direction the aircraft is actually moving over the ground
7	Track and drift	Ground track, and drift left/right caused by wind (amber when more than 15°)
8	Vertical profile	Side view: distance from home left to right, height up the side. Blue line = recent path; cyan dashed = return altitude; ground = home's height (no terrain data)
9	Aircraft and 30-second projection	The aircraft symbol faces home when you are heading towards it. The dashed line shows where you will be in 30 seconds — it turns red with an X if that is the ground
10	Glide result	REACHES HOME or SHORT by a distance, using the glide ratio set in Settings
11	Range and orientation	− / + change the map scale; HDG↑ / N↑ switches heading-up and north-up

5.3 Return-to-home panel
 
Figure 5 – Return-to-home guidance
#	Item	What it tells you / how to use it
1	Home arrow	Points to home relative to the aircraft's nose (straight up = home is ahead)
2	Turn instruction	Which way and how far to turn. Turns green with HOME AHEAD — HOLD once lined up, and OVERHEAD HOME when you arrive
3	Home data	Distance, bearing, climb/descend needed to reach the return altitude, time to home at the current ground speed, battery endurance to the reserve, whether a glide would reach home, straight-line distance and angle (for pointing a ground antenna), and where home came from
4	RTH feasibility	RTH OK (green), RTH MARGINAL (amber, less than about a minute spare) or CANNOT REACH HOME — LAND NOW (red, flashing)

Note: HOME SOURCE shows FC when home comes from the flight controller. ARM POINT, FIRST FIX or MANUAL mean the app set home itself — check it is where you launched.

5.4 Systems
 
Figure 6 – Systems gauges
#	Item	What it tells you / how to use it
1	Cell voltage	Average volts per cell, with pack voltage above. Amber and red zones follow the caution/warning voltages in Settings
2	Current	Amps drawn, with watts above
3	Throttle	Throttle percentage (when the protocol provides it)
4	Battery %	Remaining capacity. The dashed amber line is your landing reserve
5	LQ	Radio link quality. Amber below 75 %, red below 50 %
6	Link and GPS	Signal strength, signal-to-noise, transmit power, RF mode, GPS fix, satellites and position accuracy (HDOP — lower is better, under 2 is good)

5.5 Alerts
Warnings appear in red with ▲, cautions in amber with ●, and information in cyan. New warnings and cautions flash the matching MASTER button and, if enabled, sound a tone or speak the alert.
Alert	Meaning / action
TELEMETRY LOST	No data received for 2 seconds. Instruments show red flags. Fly the last known heading and wait for the link
BANK ANGLE	Bank beyond the limit — roll towards level
STALL — LOWER NOSE / ADD POWER	Airspeed below stall speed
OVERSPEED	Airspeed above never-exceed speed
SINK RATE — PULL UP	Descending faster than 8 m/s
TERRAIN — PULL UP	At the current sink rate the aircraft reaches home level within 15 seconds
TOO LOW — TERRAIN	Below the low-altitude setting while away from home
BATTERY LOW / CRITICAL	Cell voltage below the caution / warning voltage
INSUFFICIENT ENERGY TO RTH	Battery endurance is less than the time to fly home
LINK QUALITY LOW	Link quality under 50 %
GPS DEGRADED / DATA STALE	No 3D fix, fewer than 6 satellites, or GPS data has stopped
MAX RANGE EXCEEDED	Further from home than the maximum distance setting
FC: …	A message from the flight controller (MAVLink only)

6. Flying home on instruments
Practise this in the simulator (Transport → Simulator) and on clear days with a spotter before you need it.
1.	Level the wings first. Put the yellow aircraft symbol level against the horizon and stop any climb or dive. Check speed is in the white/green part of the tape.
2.	Read the return-to-home panel. Note the turn instruction and RTH feasibility. If it says CANNOT REACH HOME, pick the safest place to land now.
3.	Turn towards home. Make a gentle, steady turn (15–30° of bank) in the direction shown until the arrow points up and the panel shows HOME AHEAD — HOLD. The magenta marker on the heading tape will centre.
4.	Follow the magenta steering bars. Keep the aircraft symbol on the cross of the bars. They guide you towards home and to the return altitude.
5.	Allow for wind. Watch the green flight-path marker and track diamond. If the track drifts away from home, turn a little more into the wind until the track diamond sits on the home marker.
6.	Watch the vertical profile. Keep the aircraft above the cyan return-altitude line and the 30-second projection away from the ground. Make sure the glide line still reaches home.
7.	Arrive overhead. When the panel shows OVERHEAD HOME, circle gently at a safe height and look for the aircraft visually, or set up your landing.
Tip: If the display itself loses telemetry (LINK LOST), keep the wings level and the last heading. The instruments come back as soon as data resumes and the LOS timer shows how long you have been without data.

7. Recovery cues and failure indications
Unusual attitude
 
Figure 7 – Nose-low unusual attitude
#	Item	What it tells you / how to use it
1	Red chevrons	Appear at steep pitch angles and point back towards the horizon
2	Recovery instruction	ROLL LEVEL, NOSE HIGH — LOWER NOSE, or NOSE LOW — ROLL LEVEL, PULL. The magenta steering bars are hidden until you recover
3	Flight-path marker	Shows the real descent path
4	Altitude tape	Watch the hatched area — it is home level

Recovery: roll the wings level first, then ease the nose up to the horizon. Pulling while banked steeply only tightens a spiral.
Lost or invalid data
 
Figure 8 – Telemetry lost
#	Item	What it tells you / how to use it
1	LINK LOST	No data from the aircraft
2	GPS LOST	No current GPS data
3	MASTER WARN	Flashes until acknowledged
4	Failure flags	Red boxed labels (ATT, SPD, ALT, V/S, HDG) replace instruments that have no valid data. The app never shows old values as if they were current
5	Alert	TELEMETRY LOST in the alerts list
6	LOS timer	Time since data was lost
7	Guidance	NO GPS — FLY LAST HDG: hold the last heading until data returns

8. Flight logs and replay
Every telemetry session is logged automatically, straight from the link, so recording continues even if the display window is closed or reloaded. A session ends after 60 seconds without data; very short bench connections are discarded.
 
Figure 9 – Settings → Flight logs
#	Item	What it tells you / how to use it
1	Auto-log	On by default. Untick to log only when you press ● Log now
2	Recording status	The file currently being written and its size
3	Open log folder	Opens the logs folder in Finder / Explorer / file manager
4	Log list	Date, protocol, duration and size. ✈ = aircraft was armed; ⚠ = log was recovered after a power loss (still replayable)
5	Log buttons	▶ replay, ⬇ download (.tlog for MAVLink, otherwise .ifdrec), ✕ delete

Where logs are stored
System	Folder
Windows	%LOCALAPPDATA%\RC-IFD\logs
macOS	~/Library/Application Support/RC-IFD/logs
Linux / Raspberry Pi	~/.local/share/rc-ifd/logs

Each flight produces an .ifdrec file (exact recording, replays any protocol), a .json summary (arm/disarm, mode changes, warnings) and, for MAVLink, a .tlog that opens in Mission Planner, MAVExplorer and QGroundControl. Data is saved every second, so a sudden power cut loses at most about five seconds. The oldest logs are removed automatically when they exceed 2 GB in total or free disk space drops below 300 MB.
Replaying a flight
 
Figure 10 – Replay controls
#	Item	What it tells you / how to use it
1	Play / pause	Also the Space bar. While paused, the instruments hold their values
2	Seek bar	Drag to any point — the display rebuilds the exact aircraft state at that moment, including the trail and home
3	Speed	½× to 10×
4	✕ Live	Leave replay and return to live telemetry
5	● REC	Shows that the background logger is still recording (with elapsed time)

Note: Starting a replay disconnects the live link and closes its log, so the app asks you to confirm.

9. Settings reference
Open Settings with ⚙. Settings are saved automatically and remembered between sessions.
Setting	Default	Purpose
MAVLink: send heartbeat + requests	On	Lets the app ask the flight controller for data and home. Turn off for listen-only links
MAVLink attitude / HUD rate	10 Hz	How often attitude and speed are requested
MSP poll rate	10 Hz	How often an MSP flight controller is asked for data
Invert pitch / roll	Off (MSP pitch on)	Corrects reversed instruments — see the bench check
Units	km/h, metres, m/s	Speed, altitude/distance and vertical speed units
Stall speed / never-exceed speed	35 / 140 km/h	Colour bands on the speed tape and stall/overspeed warnings
Max bank warning	55°	Bank limit for the BANK ANGLE warning
Low altitude warning	15 m	Amber band on the altitude tape and TOO LOW warning
RTH target altitude	60 m	Return altitude for the steering bars and profile
Max home distance warn	2000 m	MAX RANGE EXCEEDED caution
Glide ratio	8 : 1	Glide ring, glide line and GLIDE HOME. Foam wing 6–8, trainer 8–10, motor-glider 12+
Cells / capacity / reserve	4S / 3000 mAh / 20 %	Battery gauges, endurance and RTH feasibility — set these for each pack
Cell caution / warn	3.5 / 3.3 V	BATTERY LOW and BATTERY CRITICAL thresholds
Audio alerts / speech	Off	Tones and spoken alerts
Telemetry stale after	2 s	How long before an instrument is flagged as failed
Frame-rate cap	Display rate (30 fps on Pi)	Lower values save power and heat

Settings also include Link statistics (data rate, corrupted frames and message rates — useful when troubleshooting) and a table showing what each protocol provides. CRSF, for example, has no wind data, and MSP's airspeed is only available on INAV with an airspeed sensor.
10. Troubleshooting
Problem	What to do
Port not listed	Press ⟳. Try another cable (some are charge-only) and port. Install the CH340 or CP210x driver if your adapter needs one
Port is busy / in use	Close Mission Planner, QGroundControl, Betaflight/INAV Configurator or Arduino — only one program can use a port
Permission denied (Linux / Pi)	sudo usermod -aG dialout $USER, then log out and back in
Connected but nothing moves	Wrong protocol or baud rate, or the flight controller port is not set to MAVLink / MSP / CRSF telemetry. Check Link statistics: Bytes/s above 0 but Frames/s at 0 means a protocol or baud mismatch
CRSF at 420000 will not open	Try 416666, or use an FTDI or CP2102N adapter (some CH340 adapters cannot run this speed)
Horizon moves the wrong way	Tick Invert pitch or Invert roll (section 4)
Airspeed shows GPS GS	The aircraft has no airspeed sensor or the protocol does not send it — the tape shows ground speed in amber
Home is in the wrong place	Use Set home here before take-off, or check the flight controller's home
macOS: app won't open	Right-click → Open, or run the xattr command in section 3
macOS: old or Python icon	Copy the new app to Applications, then run killall Dock
Display is slow on Raspberry Pi	Set the frame-rate cap to 20 or 15 fps, and make sure the Pi is not overheating or under-powered

11. Sharing the application
When passing the display on to another pilot, send the package that matches their computer together with this guide.
Recipient uses	Send
Mac (Apple Silicon)	RC-IFD.dmg (or zipped RC-IFD.app) built on an Apple Silicon Mac + this guide
Mac (Intel)	A build made on an Intel Mac + this guide
Windows	The RC-IFD folder (zipped) with run_windows.bat, or a Windows build made with build_standalone.py on Windows + this guide
Linux / Raspberry Pi	The RC-IFD folder (zipped) — they run run_mac_linux.sh or deploy/raspberrypi/install_pi.sh + this guide

Before sending
•	Remove the .venv, build and dist folders from source zips (they are recreated on first run).
•	Do not include your logs or settings folder unless you mean to share flight data.
•	Remind recipients to do the bench check and set their own battery, speed and glide-ratio settings.
•	Point them to the Quick reference card at the end of this guide for field use.
Quick reference card
Before flight	If video is lost
Connected — status bar SRC and LINK green
GPS 3D with 6+ satellites
HOME SOURCE shows FC or ARM POINT
Battery cells, capacity and reserve set
Bench check done for this aircraft
● REC showing (auto-log)	1. Wings level, stop climb/dive
2. Read RTH panel: OK / MARGINAL / CANNOT REACH
3. Turn the way shown until HOME AHEAD — HOLD
4. Keep aircraft symbol on the magenta bars
5. Correct for wind with the green track diamond
6. Keep above the cyan RTH line in the profile
7. OVERHEAD HOME: circle and look for it

Colours	Red flags	Recovery
Magenta = home / guidance
Green = normal
Cyan = your settings
Amber = caution
Red = warning	ATT SPD ALT V/S HDG = no valid data
LINK LOST = fly last heading
LOS timer = time without data	Bank > 60°: ROLL LEVEL
Nose low: roll level, then pull
Nose high: lower the nose
TERRAIN / SINK RATE: pull up

Keys	Baud rates	Logs folder
Space – replay pause
☾ – night mode
⛶ – full screen
⚙ – settings	MAVLink 57600 / 115200
CRSF 420000 (416666)
MSP 115200	Win: %LOCALAPPDATA%\RC-IFD\logs
Mac: ~/Library/Application Support/RC-IFD/logs
Linux/Pi: ~/.local/share/rc-ifd/logs

<img width="468" height="497" alt="image" src="https://github.com/user-attachments/assets/0b9bef56-0e43-4d4d-9d42-bf179e7537b5" />
