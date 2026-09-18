#!/usr/bin/env python3
"""Poll an MDOT (Mississippi Department of Transportation) traffic camera and
save each new frame. Counting happens in upload.py.

Lakeland Dr S at Treetops (home):  python capture.py --interval 5
Lakeland Dr N at Treetops:         python capture.py --stream 011403 --out cam_011403 --interval 5
"""
import argparse, csv, hashlib, time, datetime as dt, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

URL = ("https://{host}.mdottraffic.com/thumbnail?application=rtplive"
       "&streamname={stream}.stream&size=640x480&format=jpg&fitmode=stretch&t={t}")
TZ = ZoneInfo("America/Chicago")
VEHICLES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}  # COCO (Common Objects in Context) class ids


def grab(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", default="011404", help="MDOT stream id (011404 = Lakeland Dr S at Treetops)")
    ap.add_argument("--host", default="streamingjxn2", help="MDOT streaming server (streamingjxn2, streamingjxn4, ...)")
    ap.add_argument("--out", default="cam_data", help="output folder")
    ap.add_argument("--interval", type=float, default=10, help="seconds between grabs")
    ap.add_argument("--hours", type=float, default=0, help="0 = run until Ctrl+C")
    ap.add_argument("--detect", action="store_true", help="(legacy) count during capture; upload.py now does the counting")
    ap.add_argument("--conf", type=float, default=0.35, help="YOLO confidence threshold")
    a = ap.parse_args()

    out = Path(a.out)
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    log_path = out / "frames.csv"
    is_new = not log_path.exists()
    log = open(log_path, "a", newline="")
    w = csv.writer(log)
    if is_new:
        w.writerow(["timestamp_local", "epoch_ms", "file", "bytes", "md5",
                    "car", "truck", "bus", "motorcycle", "total"])

    model = None
    if a.detect:
        from ultralytics import YOLO
        model = YOLO("yolo11n.pt")  # downloads automatically on first run

    end = time.time() + a.hours * 3600 if a.hours else None
    last_hash, saved = None, 0
    while end is None or time.time() < end:
        start = time.time()
        ms = int(start * 1000)
        try:
            data = grab(URL.format(host=a.host, stream=a.stream, t=ms))
        except Exception as e:
            print(f"{dt.datetime.now(TZ):%H:%M:%S} grab failed: {e}")
        else:
            h = hashlib.md5(data).hexdigest()
            if h != last_hash and len(data) > 1000:  # skip duplicates and error stubs
                last_hash = h
                now = dt.datetime.fromtimestamp(start, TZ)
                day = frames / f"{now:%Y-%m-%d}"
                day.mkdir(exist_ok=True)
                path = day / f"{now:%H%M%S}_{ms}.jpg"
                path.write_bytes(data)

                counts = {v: "" for v in VEHICLES.values()}
                total = ""
                if model:
                    res = model(str(path), conf=a.conf, classes=list(VEHICLES), verbose=False)[0]
                    counts = {v: 0 for v in VEHICLES.values()}
                    for c in res.boxes.cls.tolist():
                        counts[VEHICLES[int(c)]] += 1
                    total = sum(counts.values())

                w.writerow([now.isoformat(timespec="seconds"), ms, path.relative_to(out).as_posix(),
                            len(data), h, counts["car"], counts["truck"], counts["bus"],
                            counts["motorcycle"], total])
                log.flush()
                saved += 1
                print(f"{now:%H:%M:%S} saved #{saved}" + (f"  vehicles={total}" if model else ""))
        time.sleep(max(0, a.interval - (time.time() - start)))
    log.close()
    print(f"done, {saved} frames saved")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("stopped")
