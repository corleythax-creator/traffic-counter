# Home cameras (cam_view.py)

Two Dahua PoE (Power over Ethernet) cameras at the house, run by `cam_view.py` on the owner's Windows desktop. Separate from the MDOT (Mississippi Department of Transportation) pipeline: nothing here is public, and nothing leaves the PC unless an upload option is turned on.

Status as of Sep 20 2026: both cameras running locally. The private Vercel page (`dashboard/home.html`) is built but not switched on (needs two Vercel settings, below). `cam_view.py` lives only on the desktop; it is not in this repo yet (see "Best practices").

## Network

```
Camera 1 (Street)  --\
                      PoE switch --- PC Ethernet port, static 192.168.1.10 / 255.255.255.0, no gateway
Camera 2           --/                PC Wi-Fi = home network 192.168.0.x (internet)
```

- The cameras are on a private link to the PC only: no internet access, not reachable from the home network. This is on purpose.
- Keep the PC's Ethernet adapter at 192.168.1.10 with no gateway, or internet traffic tries to go through the switch.
- Dahua ConfigTool finds cameras on any address and can change a camera's IP (Edit on its row).
- A camera given a 192.168.0.x address by mistake is unreachable from both sides; move it back with ConfigTool.

| | Camera 1 "Street" | Camera 2 |
|---|---|---|
| Model | Dahua IPC-HDW4431C-A-V2 (4 MP, 2.8 mm, built-in mic) | Dahua (2560x1440 main stream) |
| Address | 192.168.1.110 | 192.168.1.169 |
| Stream used | Sub Stream 2 (`subtype=2`), 720p, 15 fps, 1536 kb/s, I-frame 15 | Sub Stream 1 (`subtype=1`), 15 fps; Sub Stream 2 is capped at 1 fps while the main stream is 1440p at 25 fps |
| Dashboard | http://localhost:8080 (tab "Street") | http://localhost:8081 (tab "Camera 2") |
| Job | Vehicle and people counting, speed, motion recordings | Reads temperature/humidity from an LCD every 30 s |
| Folder | `Downloads\Traffic Count\traffic-counter-main` | `Downloads\Traffic Count\camera2` |

Other cameras on hand: MAC e0:50:8b:7f:a9:b3 (serial ...02617) was moved to 192.168.0.200 by mistake and is not in use; the one photographed with MAC ...AA:FE (serial ...02946) has not been connected.

Camera logins are admin / admin. Dahua locks the login for about 30 minutes after several failed attempts; `cam_view.py` backs off (3 s doubling to 2 min) so it cannot cause a lock by itself. Change these passwords before a camera is ever connected to the home network.

Camera settings that matter: Smart Codec off; H.264H; CBR; I-frame interval = frame rate; time zone Central with "Sync PC" (the cameras cannot reach a time server). Ignore the web page's plug-in prompt; VLC or the dashboard shows the video.

## Starting

`Traffic Count\start_cameras.bat` (double-click) runs `start_cameras.ps1`, which opens one PowerShell window per camera and then the dashboard. Closing a window stops that camera. The .ps1 holds each camera's address and password and the tab list.

Stream address: `rtsp://USER:PASSWORD@IP:554/cam/realmonitor?channel=1&subtype=N`. The script reads it over TCP (UDP froze in VLC and OpenCV).

## What cam_view.py does

One process per camera. It serves a local web page and keeps every file in the folder it is started from.

- Live view: MJPEG (Motion JPEG) stream to the page, up to `--fps` (15), with a once-a-second still underneath as fallback. Camera tabs across the top (`--name`, `--tabs "Street=8080,Camera 2=8081"`), each with a live dot.
- Pacing uses the video's own timestamps. Dahua cameras report 25-60 fps regardless of the real rate, which once made recordings play 20x fast.
- Motion recordings: pictures at 5/s with 2 s before and 5 s after, split at 60 s, plus a VLC-playable `clip.avi`. Trigger is a share of the view changing (slider), raised automatically by up to 4x when the picture is noisy. Kept 7 days or 10 GB (`--keep-days`, `--max-gb`).
- Vehicle counting across one drawn line (MOG2 background subtraction, blob tracking, pieces of one car merged, 1.5 s repeat guard, flip direction). At each crossing a small YOLO (You Only Look Once) model `yolo11n.pt` checks the spot: people, bicycles and animals are logged separately, not counted. When the model cannot tell, a shape check decides (wider than tall, sitting on the road, not huge).
- Speed: two more lines A and B with measured distances along the far and near curb; per-lane distance by where the car crosses; crossing time interpolated between frames. Tested within 1 mph on simulated cars; real accuracy depends on the tape measurements.
- Confidence score per item (model score, size in view, frames followed; shape-only capped at 69%) and per motion event (peak motion vs trigger).
- Snapshots: up to 3 wide views per item at the moments it was largest and fully in view; kept 30 days.
- Today's activity table: vehicles, people, bicycles, animals and other alerts in one sortable, filterable table with your own text labels, snapshots, recording, and icon buttons (vehicle / person / bicycle / other) that relabel an item and move it between counts.
- Learning from corrections: every crossing's measurements go to `features.csv`; the icons are the labels. After 8 of each it suggests one-line rules (including night-only ones, e.g. headlight glare), shows how many each applied rule has filtered, and suggests optional areas to ignore from day/night motion maps.
- Areas to ignore: drawn polygons excluded from both motion recording and counting. Never draw over the road where the lines are.
- Charts: last 1/2/8/24 h in 1 min/5 min/15 min/1 h steps; break down by direction, confidence or nothing; include all / medium+ / high only; side-by-side or stacked bars; touch-and-slide readout.
- Readings (`--readings`, Camera 2): boxes drawn around numbers are read every 30 s by a seven-segment LCD reader (general OCR (optical character recognition) as fallback, installed automatically), checked against a plausible range, and held back if a single reading jumps far from the recent ones until it repeats 3 times.
- Colour naming exists but is off (`COLOR_ON = False`); it was unreliable from a low camera angle.

Command-line options (most used): `--url`, `--port`, `--name`, `--tabs`, `--camera-id`, `--lan` (phone on home Wi-Fi), `--no-count`, `--no-record`, `--readings`, `--readings-every`, `--sensitivity`, `--count-min-area`, `--count-gap`, `--keep-days`, `--max-gb`, `--fps`, `--cloud`, `--upload-counts`. Run `python cam_view.py --help` for all.

## Files it writes (in the folder it runs from)

| File | Contents | Kept |
|---|---|---|
| `motion/DATE/TIME/` | recording pictures, `clip.avi`, `thumb.jpg`, `event.json`, `vehicles.json`, `others.json` | 7 days / 10 GB |
| `snapshots/DATE/` | 1-3 pictures per item | 30 days |
| `counts.csv` | every vehicle: time, direction, recording, picture no., confidence, type, max motion % | forever |
| `others.csv` | people, bicycles, animals, other alerts, filtered and rejected items | forever |
| `speeds.csv` | measured speeds | forever |
| `features.csv` | measurements of every crossing (learning) | forever |
| `readings.csv` | Camera 2 readings: time, name, value, unit, raw text, OCR confidence | forever |
| `counting.json` | line, direction names, speed lines, rules | settings |
| `settings.json` | sensitivity sliders, areas to ignore | settings |
| `labels.json`, `notes.json`, `reviewed.json`, `corrections.json` | your answers, text labels, reviewed marks, undo history | forever (reviewed 30 days) |
| `readings.json`, `reading_crops/` | reading boxes, last picture read per box | settings |
| `motion_heat.npz` | day/night motion maps for area suggestions | rolling |

Back these up: the CSV and JSON files. The recordings can be lost without harm.

## Supabase and Vercel (optional uploads, off by default)

- `--upload-counts`: 5-minute totals into `traffic_video_counts` with `camera = --camera-id` (only counts, never pictures).
- `--cloud`: latest picture every 2 s and finished motion events to the private storage bucket `homecam`, plus `homecam_status` and `homecam_events` (see `docs/schema.sql`).
- Private page `dashboard/home.html` (password sign-in, signed links that expire; routes in `dashboard/api/homecam/`, helpers in `dashboard/lib/homecam.js`). To switch it on, add Vercel env vars `SUPABASE_SERVICE_KEY` and `HOMECAM_PASSWORD` and redeploy. Until then its API answers "Not set up yet".

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 Unauthorized` in a camera window | wrong login, or the camera's 30-minute lockout; leave it running, it reconnects |
| Page says "Can't reach the camera" | address on the wrong network (ConfigTool), switch power/uplink, or password |
| Choppy or 20x-speed playback | camera stream frame rate too low; set the substream to 15 fps |
| Nothing opens from `start_cameras.bat` | run it from PowerShell to see the message; `Unblock-File` on a freshly downloaded `.ps1` |
| Double counts | split cars (merged now), or headlights at night (night-only glare rule) |
| Kids/pets counted | the model check sends them to People/Animals; relabel with the icons if one slips through |
| Readings wrong | box too loose/tight or digits too small; check the small picture beside each value |

## Best practices going forward

1. One copy of the code, in git. Put `cam_view.py` and the start scripts in this repo (for example `home/`), clone the repo on the desktop instead of using ZIP copies, and run the same file for both cameras with each camera's folder as the working directory. Today there are three hand-copied versions (`traffic-counter-main`, `camera2`, and the older `traffic-counter`).
2. Keep secrets out of files that get shared or committed: camera addresses with passwords go in a local, git-ignored settings file; `cam_view.py` reads `--url` or `CAM_URL`.
3. Change the camera passwords from admin/admin.
4. Back up the CSV/JSON files weekly (OneDrive or an external drive), not the recordings.
5. One change at a time, then check the dashboard and the camera windows for a day before the next.
6. Keep labeling with the icons; the learning panel only improves with answers on both correct and wrong counts.
7. Keep this file and `CLAUDE.md` current whenever a camera, address, option or file changes.
