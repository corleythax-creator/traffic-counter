#!/usr/bin/env python3
"""Run every camera continuously on one computer (instead of GitHub's 15-minute samples).

  python run_local.py

Starts a capture for each snapshot camera (every image the camera publishes),
counts and uploads them every 2 minutes with one shared model, and keeps the
live-video counter running in back-to-back 5-minute windows. Images are deleted
once uploaded. While this runs, the GitHub schedule sees fresh data and skips
these cameras automatically; if this computer stops, GitHub takes over again.
Press Ctrl+C to stop everything.
"""
import json, subprocess, sys, threading, time
from pathlib import Path

import upload

HERE = Path(__file__).parent
cfg = json.loads((HERE / "cameras.json").read_text()) if (HERE / "cameras.json").exists() else {}
SNAPSHOT_CAMS = ["lakeland-treetops", "jackson-e-fraternity", "jackson-w-fraternity", "lakeland-n-airport",
                 "university-w-lamar"] + [c for c in cfg.get("sample", []) if c not in
                 ("lakeland-treetops", "jackson-e-fraternity", "jackson-w-fraternity", "lakeland-n-airport", "university-w-lamar")]
VIDEO_CAMS = list(cfg.get("video", {}))
UPLOAD_EVERY = 120   # seconds between count-and-upload passes
VIDEO_WINDOW = 300   # seconds of video per counting window

stop = threading.Event()


def video_loop(cam):
    while not stop.is_set():
        r = subprocess.run([sys.executable, str(HERE / "video.py"), "--camera", cam, "--seconds", str(VIDEO_WINDOW)],
                           capture_output=True, text=True)
        out = (r.stdout.strip() or r.stderr.strip()[-300:])
        print(f"{time.strftime('%H:%M')} video {cam}: {out.splitlines()[-1] if out else 'no output'}")
        if r.returncode != 0:
            stop.wait(30)  # stream hiccup: pause briefly, then try again


def main():
    url, key = upload.load_env()
    procs = []
    for n, cam in enumerate(SNAPSHOT_CAMS):
        c = upload.CAMERAS[cam]
        procs.append(subprocess.Popen([sys.executable, str(HERE / "capture.py"), "--host", c["host"], "--stream", c["stream"],
                                       "--out", str(HERE / f"cam_{cam}"), "--interval", "5"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(5 / len(SNAPSHOT_CAMS))  # spread start times so cameras never share a timestamp
    print(f"Capturing {len(SNAPSHOT_CAMS)} cameras; video counting on {', '.join(VIDEO_CAMS) or 'none'}")
    for v in VIDEO_CAMS:
        threading.Thread(target=video_loop, args=(v,), daemon=True).start()

    from ultralytics import YOLO
    model = YOLO(upload.MODEL_NAME)
    try:
        while True:
            start = time.time()
            for cam in SNAPSHOT_CAMS:
                try:
                    upload.run_once(HERE / f"cam_{cam}", url, key, model, cam, 0.20, False)
                except Exception as e:
                    print(f"{time.strftime('%H:%M')} upload failed for {cam}: {e}")
            for i, p in enumerate(procs):  # restart any capture that died
                if p.poll() is not None:
                    cam = SNAPSHOT_CAMS[i]; c = upload.CAMERAS[cam]
                    procs[i] = subprocess.Popen([sys.executable, str(HERE / "capture.py"), "--host", c["host"],
                                                 "--stream", c["stream"], "--out", str(HERE / f"cam_{cam}"), "--interval", "5"],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    print(f"restarted capture for {cam}")
            time.sleep(max(5, UPLOAD_EVERY - (time.time() - start)))
    except KeyboardInterrupt:
        print("stopping...")
    finally:
        stop.set()
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
