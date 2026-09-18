#!/usr/bin/env python3
"""One sampling run for GitHub Actions: capture each camera for a few minutes
in parallel, then count and upload.

The cameras to sample are listed in SAMPLE_CAMERAS below plus the "sample" list
in cameras.json (keys from upload.py or cameras.json). This takes precedence over
the CAMERAS variable in the workflow file."""
import json, os, subprocess, sys, time
os.environ["TRAFFIC_SOURCE"] = "github"  # marks rows from the scheduled samples
from pathlib import Path
from upload import CAMERAS

SAMPLE_CAMERAS = ["jackson-e-fraternity", "jackson-w-fraternity", "lakeland-n-airport", "university-w-lamar"]
_cfg = Path(__file__).with_name("cameras.json")
if _cfg.exists():  # cameras.json can add to the list without editing this file
    SAMPLE_CAMERAS += [c for c in json.loads(_cfg.read_text()).get("sample", []) if c not in SAMPLE_CAMERAS]
INTERVAL = 5  # seconds between grabs

cams = SAMPLE_CAMERAS or [c.strip() for c in os.environ.get("CAMERAS", "").split(",") if c.strip()]
minutes = float(os.environ.get("SAMPLE_MINUTES", "4"))

# Skip cameras that another computer (run_local.py) is already covering continuously
from datetime import datetime, timezone
import upload
try:
    _url, _key = upload.load_env()
    def _fresh(path, field, extra=0):
        rows = upload.get_json(_url, _key, path)
        if not rows:
            return False
        t = datetime.fromisoformat(rows[0][field].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds() - extra < 10 * 60
    live = [c for c in cams if _fresh(f"traffic_frames?camera=eq.{c}&source=eq.local&select=captured_at&order=captured_at.desc&limit=1", "captured_at")]
    if live:
        print("covered by a continuous computer, skipping:", ", ".join(live))
    cams = [c for c in cams if c not in live]
except Exception as e:
    print(f"could not check for a continuous computer: {e}")

procs = []
for n, c in enumerate(cams):
    if n:
        time.sleep(INTERVAL / len(cams))  # spread start times so cameras never share a timestamp
    cfg = CAMERAS[c]
    procs.append(subprocess.Popen([sys.executable, "capture.py", "--host", cfg["host"], "--stream", cfg["stream"],
                                   "--out", f"cam_{c}", "--interval", str(INTERVAL), "--hours", str(minutes / 60)]))
# live-video cameras (cameras.json "video") count line crossings while the snapshots run
video_cams = list(json.loads(_cfg.read_text()).get("video", {})) if _cfg.exists() else []
try:  # a continuous computer's video windows end every 5 minutes; skip if one ended recently
    video_cams = [v for v in video_cams if not _fresh(
        f"traffic_video_counts?camera=eq.{v}&source=eq.local&select=started_at,seconds&order=started_at.desc&limit=1", "started_at", 300)]
except Exception:
    pass
video_procs = [(v, subprocess.Popen([sys.executable, "video.py", "--camera", v,
                                     "--seconds", str(max(60, minutes * 60 - 30))])) for v in video_cams]
for p in procs:
    p.wait()

failed = 0
for c in cams:
    r = subprocess.run([sys.executable, "upload.py", "--camera", c, "--out", f"cam_{c}"], capture_output=True, text=True)
    print(r.stdout.strip()); print(r.stderr.strip()[-2000:], file=sys.stderr)
    if "upload failed" in r.stdout or r.returncode != 0:
        failed += 1
for v, p in video_procs:
    if p.wait() != 0:
        print(f"video count failed for {v}"); failed += 1
sys.exit(1 if failed else 0)
