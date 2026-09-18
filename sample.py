#!/usr/bin/env python3
"""One sampling run for GitHub Actions: capture each camera for a few minutes
in parallel, then count and upload.

The cameras to sample are listed in SAMPLE_CAMERAS below plus the "sample" list
in cameras.json (keys from upload.py or cameras.json). This takes precedence over
the CAMERAS variable in the workflow file."""
import json, os, subprocess, sys, time
from pathlib import Path
from upload import CAMERAS

SAMPLE_CAMERAS = ["jackson-e-fraternity", "jackson-w-fraternity", "lakeland-n-airport", "university-w-lamar"]
_cfg = Path(__file__).with_name("cameras.json")
if _cfg.exists():  # cameras.json can add to the list without editing this file
    SAMPLE_CAMERAS += [c for c in json.loads(_cfg.read_text()).get("sample", []) if c not in SAMPLE_CAMERAS]
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
