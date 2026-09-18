#!/usr/bin/env python3
"""One sampling run for GitHub Actions: capture each camera for a few minutes
in parallel, then count and upload.

The cameras to sample are listed in SAMPLE_CAMERAS below (keys from upload.py).
This list takes precedence over the CAMERAS variable in the workflow file, so
cameras can be added by editing this file alone."""
import os, subprocess, sys, time
from upload import CAMERAS

SAMPLE_CAMERAS = ["jackson-e-fraternity", "jackson-w-fraternity", "lakeland-n-airport", "university-w-lamar"]
INTERVAL = 5  # seconds between grabs

cams = SAMPLE_CAMERAS or [c.strip() for c in os.environ.get("CAMERAS", "").split(",") if c.strip()]
minutes = float(os.environ.get("SAMPLE_MINUTES", "4"))

procs = []
for n, c in enumerate(cams):
    if n:
        time.sleep(INTERVAL / len(cams))  # spread start times so cameras never share a timestamp
    cfg = CAMERAS[c]
    procs.append(subprocess.Popen([sys.executable, "capture.py", "--host", cfg["host"], "--stream", cfg["stream"],
                                   "--out", f"cam_{c}", "--interval", str(INTERVAL), "--hours", str(minutes / 60)]))
for p in procs:
    p.wait()

failed = 0
for c in cams:
    r = subprocess.run([sys.executable, "upload.py", "--camera", c, "--out", f"cam_{c}"], capture_output=True, text=True)
    print(r.stdout.strip()); print(r.stderr.strip()[-2000:], file=sys.stderr)
    if "upload failed" in r.stdout or r.returncode != 0:
        failed += 1
sys.exit(1 if failed else 0)
