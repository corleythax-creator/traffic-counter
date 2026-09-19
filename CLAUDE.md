# Traffic counter

Counts vehicles (and, roughly, people) on public MDOT (Mississippi Department of Transportation) traffic cameras in Jackson and Oxford, Mississippi, stores every count in Supabase, and shows it on a public Vercel dashboard.

- Public dashboard: https://traffic-counter-dashboard.vercel.app (Vercel project `traffic-counter-dashboard`, root directory `dashboard/`, auto-deploys on push to `main`)
- Database: Supabase project `fuel-model` (ref `wcrsomethkjlfhhuurmh`, Pro plan). It also holds unrelated land-records tables; only touch the `traffic_*` and `camera_refs` objects.
- Repo: `corleythax-creator/traffic-counter` (public)

## How it runs

There are two collectors. Both write the same tables, tagged by `source`.

| Collector | Where | Coverage | Tag |
|---|---|---|---|
| `run_local.py` | Owner's Windows desktop, always-on | Every camera image (~4 frames/min/camera) plus continuous video counting | `source = 'local'` |
| `sample.py` via `.github/workflows/sample.yml` | GitHub Actions, cron every 15 min | ~4 min of each 15 (~65 frames/hr/camera) plus ~3.5 min of video | `source = 'github'` |

GitHub is the backup: before each run, `sample.py` checks Supabase and skips any camera that has a `source='local'` frame (or video window) from the last 10 minutes. If the desktop stops, GitHub resumes automatically on its next run. No config change is needed to switch between them.

Pipeline per snapshot camera:

1. `capture.py` polls the MDOT thumbnail URL every 5 s, skips duplicate images by MD5 (message-digest 5 hash), saves new JPEGs plus a row in `cam_<slug>/frames.csv`.
2. `upload.py` (`run_once`) counts each saved frame with YOLO11 (You Only Look Once) medium at 960 px, applies the counting zone and filters, checks the camera view against its reference, upserts `traffic_frames` and `traffic_detections`, then deletes the image. Progress is checkpointed in `cam_<slug>/.last_upload`.

Pipeline for the video camera: `video.py` opens the HLS (HTTP Live Streaming) feed with OpenCV, uses every third frame (~10 fps), finds moving vehicles by MOG2 background subtraction (no neural network), tracks blobs by nearest predicted position, and counts each track once when it crosses a counting line. Writes one row per line and direction to `traffic_video_counts`.

## Repo map

```
capture.py        Save new frames from one camera (--host --stream --out --interval --hours)
upload.py         Count + upload frames; holds CAMERAS, zones, thresholds, camera-move detection
video.py          Line-crossing counter for live video cameras
sample.py         One GitHub Actions run: parallel captures, then counts; skips cameras covered locally
run_local.py      Continuous all-camera runner for a PC (captures, 2-min upload loop, video loop)
run_local.bat     Windows launcher: git pull, then run_local.py, and hold the window open
cameras.json      Extra/overriding cameras, "sample" list for GitHub, "video" cameras and lines
requirements.txt  ultralytics, numpy, pillow (workflow installs CPU torch + opencv-python-headless)
.github/workflows/sample.yml   Cron */15, 14-min timeout, secrets SUPABASE_URL / SUPABASE_KEY
dashboard/
  index.html      Main page: city groups, camera cards (thumbnail, now, 5/15/60-min avgs), live-video player, charts
  camera.html     Per-camera detail page (?cam=<slug>&w=1h|6h|24h|7d), per-minute bars
  api/traffic.js  -> Supabase RPC traffic_dashboard(win)
  api/camera.js   -> Supabase RPC traffic_camera(cam, win)
  api/snapshot.js -> proxies the MDOT thumbnail for a camera (15 s CDN cache)
  api/stream.js   -> proxies the HLS live stream (playlist rewritten so segments
                     come back through this endpoint; only cameras in its own CAMS,
                     only bare filenames inside that camera's stream directory)
docs/schema.sql   Database objects used by this project (reference, not a migration)
```

## Cameras

Snapshot URL: `https://{host}.mdottraffic.com/thumbnail?application=rtplive&streamname={stream}.stream&size=640x480&format=jpg&fitmode=stretch&t={ms}`
Video URL: `https://{host}.mdottraffic.com/rtplive/{stream}.stream/playlist.m3u8` (1280x720, 30 fps)

| Slug | Name | Stream | Host | City | Type | Defined in |
|---|---|---|---|---|---|---|
| `lakeland-treetops` | Lakeland Dr S at Treetops Blvd | 011404 | streamingjxn2 | Jackson | snapshot | upload.py |
| `lakeland-n-airport` | Lakeland Dr N at Airport Rd | 010102 | streamingjxn2 | Jackson | snapshot | upload.py |
| `jackson-e-fraternity` | Jackson E at Frtrnty | 060106 | streamingjxn4 | Oxford | snapshot | upload.py, zone overridden in cameras.json |
| `jackson-w-fraternity` | Jackson W at Frtrnty | 060105 | streamingjxn4 | Oxford | snapshot | upload.py, zone overridden in cameras.json |
| `university-w-lamar` | University W at Lamar | 060204 | streamingjxn4 | Oxford | snapshot | upload.py |
| `lamar-n-university` | Lamar Blvd N at University Ave (PTZ) | 060202 | streamingjxn4 | Oxford | snapshot | cameras.json |
| `university-e-ms7` | University Ave E at MS 7 (PTZ) | 060205 | streamingjxn4 | Oxford | video | cameras.json "video" |
| `lakeland-n-treetops` | Lakeland Dr N at Treetops Blvd | 011403 | streamingjxn2 | Jackson | defined, not collected | upload.py |

Finding a stream ID: MDOT's mobile site lists sites at `https://mobile.mdottraffic.com/listCamLogicalSites.aspx?sublocationid=<n>`, but the stream ID is not in the page. The reliable way is to probe `streamname=0XXXYY` thumbnails and OCR (optical character recognition) or read the name overlay at the bottom of the image.

Camera slugs appear in several places that must stay in sync when adding or renaming a camera: `upload.py` CAMERAS or `cameras.json`, `run_local.py` SNAPSHOT_CAMS (plus cameras.json "sample"), `sample.py` SAMPLE_CAMERAS (plus cameras.json "sample"), `dashboard/index.html` CAMS, `dashboard/camera.html` CAMERAS, `dashboard/api/snapshot.js` CAMS.

## Counting method (upload.py)

- Model `yolo11m.pt` at `IMGSZ = 960`, classes car/motorcycle/bus/truck plus person (COCO ids 2, 3, 5, 7, 0). All detections down to `DET_FLOOR = 0.10` are stored.
- A vehicle counts if its box's bottom-center is inside the camera's zone and it passes `counts_as_vehicle`: confidence >= 0.20 (`--conf`); not a flat road marking (height < 14 px and width/height > 1.7); not a low-confidence (< 0.35) box touching two image edges; tile-only detections need >= 0.30 (tiles are off by default, `--tiles`).
- People: any person box with confidence >= 0.25 anywhere above the name overlay. Counted separately in `people`; unreliable on these cameras because people are 10 to 20 px tall.
- Zones are lists of (x, y) fractions of the image from the top-left, drawn per camera view.

Model choices were tested on real frames: medium@960 beat nano, small, and large at 640/960/1280 for recall per second. Zoomed tiles did not help with the medium model. Extra-large@1280 catches ~25-30% more cars in dense queues but is ~5x slower; not enabled.

## Camera-move detection

MDOT operators pan and zoom cameras. `upload.py` saves a 320x240 grayscale reference image and the zone to `camera_refs` (video cameras use key `video:<slug>`). The reference is the **median of `REF_FRAMES` (9) daylight frames**, not a single frame: traffic and headlight glare sit in different places from frame to frame and drop out of the median, leaving the poles, lane markings and crosswalk that the matcher actually needs. Every 20 frames it matches SIFT (scale-invariant feature transform) features against the reference with a RANSAC (random sample consensus) homography:

- `same`: counts with the stored zone.
- `shifted`: small pan/zoom; the zone is warped to follow.
- `changed`: no reliable match; counts with `FALLBACK_ZONE` (lower 75% of the frame). Video cameras skip counting and write rows with `vehicles = null`.
- `night`: infrared (near-zero color saturation) frames skip the check and use the stored zone.

**SIFT cannot match across a day/night change, however good the reference is.** Measured Sep 19 on cameras that had not moved: a reference built from night frames matched 0 of 9 daylight frames, while a daylight median of those same 9 frames matched 9 of 9. So one reference cannot serve both halves of the day, and a reference always stops matching at dawn and again at dusk.

A reference older than `REF_MAX_AGE_H` (6 h) is therefore re-based whichever way its check went:

- on `same`, it keeps the zone that was just confirmed to fit;
- on `changed`, it is rebuilt from what the camera sees now, with the base zone from the code -- the same recovery a person would do by deleting the `camera_refs` row.

The `changed` half matters: re-basing only on `same` deadlocks, because recovery needs a passing check and the check cannot pass. That bug put five of six cameras on the fallback zone for the whole morning of Sep 19.

A reference younger than 6 h is never re-based, so a camera that really has been re-aimed stays visibly `changed` rather than quietly becoming its own reference. The cost of the dawn and dusk transitions is one check interval (~20 frames) on the fallback zone; the cost of a real move going unnoticed is capped at 6 hours. A batch with fewer than `REF_MIN_FRAMES` (5) usable frames is skipped rather than medianed, so a reference is never built from one or two frames.

The zone in `camera_refs` is what both collectors use. To redraw a zone for a re-aimed camera, update `camera_refs` (new ref image + zone) and also update the base zone in `upload.py`/`cameras.json` so any future reference saves use it. Deleting a `camera_refs` row makes the next full daylight batch the new reference with the code's base zone.

## Database

See `docs/schema.sql`. Main objects:

- `traffic_frames`: one row per frame, PK `(camera, epoch_ms)`. Counts, `people`, `model`, `view_status`, `source`, `brightness`, `conf_threshold`.
- `traffic_detections`: one row per detected object, PK `(camera, epoch_ms, idx)`, FK to frames. Box coordinates are 0-1 fractions. Kept indefinitely by the owner's choice (about 45 MB/day at full coverage).
- `traffic_video_counts`: PK `(camera, started_at, line, direction)`. Rate per hour = `vehicles / seconds * 3600`.
- `camera_refs`: reference view + zone per camera.
- RPC functions for the dashboard (security definer, read-only, granted to `anon`): `traffic_dashboard(win)` and `traffic_camera(cam, win)`.
- `purge_old_traffic_detections(keep_days)`: manual cleanup only; the pg_cron job was removed on purpose.

Security model: collectors write with the service-role key. The dashboard only has the publishable key, which can call the two RPC functions and cannot read tables (RLS (row-level security) is on with no policies).

History notes: before Sep 18 2026 ~5 PM Central, `lakeland-treetops` was recorded continuously by an older script with `yolo11n.pt`, whole image, 0.35 cutoff and no zone (`model = 'yolo11n.pt'`, `source` null). Its counts run low compared with later rows; the dashboard shows a note when the latest frame is from that model.

## Secrets and environment

- Local `.env` next to the scripts (never commit): `SUPABASE_URL=https://wcrsomethkjlfhhuurmh.supabase.co`, `SUPABASE_KEY=<service_role key>`. `load_env()` tolerates UTF-16/BOM files and invisible characters, which happened on Windows.
- GitHub Actions secrets: `SUPABASE_URL`, `SUPABASE_KEY`.
- Vercel env: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (publishable key; safe to expose, RPC-only).
- `TRAFFIC_SOURCE` env var sets the `source` tag (`sample.py` sets `github`; default `local`).

## Common tasks

Run everything on a PC:
```
python -m pip install ultralytics tzdata
python run_local.py
```
On Windows, double-click `run_local.bat` instead: it pulls the latest code first, then starts the collector, and keeps the window open so a crash is readable. Ctrl+C stops it.

Windows notes: install `tzdata` (zoneinfo needs it), keep the folder out of OneDrive, quote paths with spaces, set sleep to Never. The desktop copy must be a `git clone`, not an extracted ZIP, or it cannot update itself.

Add a snapshot camera:
1. Fetch a 640x480 frame, draw a zone (fractions), test counts on a few frames and visually check boxes.
2. Add it under `cameras.json` "cameras" and "sample".
3. Add it to `run_local.py` SNAPSHOT_CAMS if it should run on the desktop (cameras.json "sample" entries are already included).
4. Add it to `dashboard/index.html` CAMS (with its city group), `dashboard/camera.html` CAMERAS, and `dashboard/api/snapshot.js` CAMS.

Add a video camera: add under cameras.json "video" with `crop` [x, y, w, h] in 1280x720 pixels and `lines` {name: [[x1,y1],[x2,y2]]} drawn across each road. Direction "toward" = crossing toward the camera side of the line. Validate by making a contact sheet of every counted crossing from a 1 to 2 minute clip. Update `drawVideo` in `dashboard/index.html` (it filters `university-e-ms7`).

Change a dashboard query: edit the SQL function in Supabase (keep it security definer, read-only, `grant execute ... to anon`), then the page. `dashboard/api/*.js` only forwards to the RPC with a short CDN cache.

Preview the dashboard: serve `dashboard/` and mock `/api/*`, or render with Playwright against the live API responses.

## Constraints and gotchas

- GitHub Actions job timeout is 14 minutes with a 15-minute cron and a concurrency group; runs can start late. Keep `SAMPLE_MINUTES` plus counting inside that.
- The repo is public so Actions minutes are free; continuous 24/7 jobs on Actions are a terms-of-service gray area, which is why the desktop collector exists.
- Captures from multiple cameras are staggered (`INTERVAL / len(cams)`) so timestamps never collide.
- `upload.py` never re-uploads a frame whose image has been deleted, so deleting `.last_upload` to recount cannot blank out existing rows.
- MDOT cameras marked PTZ (pan-tilt-zoom) get re-aimed often; watch `view_status`.
- Known accuracy limits: dense queues are undercounted (cars merge or are hidden; far queue is outside zones); people counts are a rough index only.
- Night is not "rougher", it is close to blind on the cameras that switch to infrared. Measured Sep 18: `lakeland-n-airport` went from 16.6 detections/frame in colour to 0.34 in infrared (98% loss), `lakeland-treetops` 10.4 to 2.6 (75%). The Oxford cameras never switched to infrared that night and held steady, so Jackson-vs-Oxford after dark is not a like-for-like comparison. `DET_FLOOR` is already 0.10 and there is nothing there to find, so lowering the confidence threshold does not recover it; only a model that handles infrared, or a motion-based counter like `video.py`, would. `view_status = 'night'` marks these frames and the dashboard fades them.
- `view_status = 'changed'` means the frame was counted with `FALLBACK_ZONE` (lower 75%), which is looser than a drawn zone and reads high: on Sep 18 the drawn zones kept 60-87% of detections where the fallback kept 81-99%. Before checking anything else, confirm the camera has actually moved by eye: single-frame references made this status mostly false. Measured Sep 19 on unmoved views, single-frame reference vs median-of-9: `jackson-e-fraternity` 10-15 inliers against a cutoff of 15 (passing 4 of 10 frames) became 71-147 (10 of 10), and every other camera improved 2-5x. Over the preceding 6 hours that flapping had put `jackson-e-fraternity` on the fallback zone for 83% of frames and the other three non-infrared cameras for 29% each, with no camera having moved. References now re-base themselves every 6 hours in either direction, so a `changed` run lasting more than one check interval means either a reference younger than 6 hours (a real move, still inside its grace period) or a collector that is not running the current code. Deleting the `camera_refs` row still forces an immediate rebuild from the code's base zone.
- Earlier Claude-hosted dashboards (claude.ai artifacts) read Supabase through the owner's connector; the Vercel dashboard is the maintained one.

## Style for user-facing text

The owner prefers acronyms spelled out on first use, e.g. MDOT (Mississippi Department of Transportation). Times are shown in Central time (`America/Chicago`).
