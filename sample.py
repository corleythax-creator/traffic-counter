#!/usr/bin/env python3
"""One sampling run for GitHub Actions: capture each camera for a few minutes
in parallel, then count and upload. Cameras come from the CAMERAS env var
(comma-separated keys from upload.py), defaulting to the Jackson/Fraternity pair."""
import os, subprocess, sys, time
from upload import CAMERAS

cams = [c.strip() for c in os.environ.get("CAMERAS", "jackson-e-fraternity,jackson-w-fraternity").split(",") if c.strip()]
minutes = float(os.environ.get("SAMPLE_MINUTES", "4"))

procs = []
for n, c in enumerate(cams):
    if n:
        time.sleep(2.5)  # stagger so two cameras never share a timestamp
    cfg = CAMERAS[c]
    procs.append(subprocess.Popen([sys.executable, "capture.py", "--host", cfg["host"], "--stream", cfg["stream"],
                                   "--out", f"cam_{c}", "--interval", "5", "--hours", str(minutes / 60)]))
for p in procs:
    p.wait()

failed = 0
for c in cams:
    r = subprocess.run([sys.executable, "upload.py", "--camera", c, "--out", f"cam_{c}"], capture_output=True, text=True)
    print(r.stdout.strip()); print(r.stderr.strip()[-2000:], file=sys.stderr)
    if "upload failed" in r.stdout or r.returncode != 0:
        failed += 1
sys.exit(1 if failed else 0)
