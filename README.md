# Traffic counter

Samples MDOT (Mississippi Department of Transportation) traffic cameras every 15 minutes with GitHub Actions, counts vehicles with YOLO11 nano (whole image plus zoomed tiles, per-camera counting zones, filters for road markings and signs), and uploads counts and individual detections to Supabase (`traffic_frames`, `traffic_detections`).

- `sample.py`: one sampling run (captures each camera for `SAMPLE_MINUTES`, then counts and uploads)
- `capture.py`: saves new frames from one camera
- `upload.py`: counts saved frames and uploads them; camera list and counting zones live in `CAMERAS`

Required repository secrets: `SUPABASE_URL`, `SUPABASE_KEY` (service role key). Cameras sampled are set by `CAMERAS` in `.github/workflows/sample.yml`.
