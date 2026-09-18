# Traffic counter

Counts vehicles on public MDOT (Mississippi Department of Transportation) traffic cameras in Jackson and Oxford, Mississippi, and uploads counts and individual detections to Supabase (`traffic_frames`, `traffic_detections`, `traffic_video_counts`).

Snapshot cameras are counted with YOLO11 (You Only Look Once) medium at 960 pixels, using a per-camera counting zone and filters for road markings and pavement edges. Zoomed tiles are supported but off by default (`--tiles`); the medium model rarely needs them. One live-video camera is counted separately by line crossings, without a neural network.

Public dashboard: https://traffic-counter-dashboard.vercel.app

## Scripts

- `sample.py`: one sampling run for GitHub Actions (captures each camera for `SAMPLE_MINUTES`, then counts and uploads)
- `run_local.py`: continuous all-camera runner for an always-on computer
- `capture.py`: saves new frames from one camera
- `upload.py`: counts saved frames and uploads them; camera list and counting zones live in `CAMERAS`
- `video.py`: line-crossing counter for live-video cameras

The two collectors write the same tables, tagged by `source` (`local` or `github`). Before each scheduled run, `sample.py` skips any camera that an always-on computer has covered in the last 10 minutes, so GitHub acts as a backup with no config change.

## Configuration

Cameras sampled on GitHub are `SAMPLE_CAMERAS` in `sample.py` plus the `"sample"` list in `cameras.json`. New cameras and zone overrides go in `cameras.json`.

Required repository secrets: `SUPABASE_URL`, `SUPABASE_KEY` (service role key).

See `CLAUDE.md` for the full camera list, counting method, database schema, and the steps for adding a camera.
