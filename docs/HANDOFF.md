# Handoff

## Status (Sep 20 2026)

Running: seven MDOT (Mississippi Department of Transportation) cameras (two in Jackson, five in Oxford; one of the Oxford cameras counted from live video), collected by the owner's desktop (`run_local.py`) with GitHub Actions (`sample.py`, every 15 minutes) as the automatic backup. Counts go to Supabase project `fuel-model`; the public dashboard is https://traffic-counter-dashboard.vercel.app. The New Orleans camera was removed on Sep 20.

## Open items, most important first

1. Desktop: replace the unzipped copy in `Downloads\Traffic Count\traffic-counter-main` with a git clone (see README "Running on the desktop"), so `run_local.bat` can update itself. The unzipped copy was running old code on Sep 20 (it re-ran the video counter back-to-back instead of pacing).
2. Night counting: Jackson cameras switch to infrared and the model misses most vehicles (75-98% fewer detections). Options: an infrared-capable model, or motion-based counting like `video.py` for those cameras.
3. The video camera pauses for up to an hour at dusk and dawn while its reference view re-bases (a daylight reference cannot match a night view). Acceptable for now; a separate night reference would remove the gap.
4. Consider `yolo11x` at 1280 px for the Lakeland cameras' dense queues (about 25-30% more cars, about 5x slower).
5. Dashboard ideas not yet built: "compared to normal" for each camera, and a day-by-hour heat map.
6. Old New Orleans rows remain in `traffic_video_counts` and `camera_refs` (`video:i10-orleans`); delete them only if the owner asks.

## Prompt for whoever takes this over

Paste this, with the repository attached or cloned:

---
You are taking over Corley's public city traffic counter, `corleythax-creator/traffic-counter`. Read `README.md`, `CLAUDE.md` (the detailed guide: cameras, pipeline, database, security model, measured gotchas) and `docs/HANDOFF.md` first.

What it is: YOLO11 medium counts vehicles in MDOT traffic camera snapshots inside drawn zones, and a motion-based line-crossing counter handles one live-video camera. A Windows desktop runs `run_local.py` continuously; GitHub Actions runs `sample.py` every 15 minutes as a backup and skips cameras the desktop is covering. Everything lands in Supabase (`traffic_frames`, `traffic_detections`, `traffic_video_counts`, `camera_refs`), and a Vercel dashboard reads it through two read-only RPC (remote procedure call) functions with the publishable key.

How to work with Corley:
- He wants working results: deliver complete files or single copy-paste commands; do not ask him to hand-edit files.
- Test before handing over (render the dashboard with Playwright against the live API, run counting on real frames), and say what was and was not tested.
- Keep `CLAUDE.md`, `docs/schema.sql` and this file current in the same change as the code. Record measurements behind decisions so they are not undone.
- Spell out acronyms on first use; show times in Central.
- Never commit secrets. The service-role key lives only in `.env`, GitHub secrets and nowhere else.
- His private home cameras are a separate project (`home-cameras`); do not mix them in here.

First task: go through the open items in `docs/HANDOFF.md` with him, starting at the top.
---
