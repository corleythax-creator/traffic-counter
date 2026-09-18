#!/usr/bin/env python3
"""Count vehicles on frames saved by capture.py and upload them to Supabase.

Each frame is checked twice: once whole, and once as 9 overlapping tiles
enlarged 2x, which finds smaller and more distant vehicles. Only vehicles whose
bottom-center falls inside the camera's counting zone (the near and middle
stretch of road) count toward the totals. Every detection down to 0.10
confidence is still stored in traffic_detections so the cutoff can be re-tuned.

Safe to re-run and safe while capture.py is running: it resumes after the last
uploaded frame (cam folder/.last_upload) and overwrites instead of duplicating.
Needs SUPABASE_URL and SUPABASE_KEY in a .env file next to this script.

  Lakeland Dr S (home):  python upload.py --every 60
  Lakeland Dr N:         python upload.py --camera lakeland-n-treetops --out cam_011403 --every 15
"""
import argparse, csv, json, os, time, urllib.request
from pathlib import Path

VEHICLES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
MODEL_NAME = "yolo11n.pt"
DET_FLOOR = 0.10
# Counting zones as (x, y) fractions of the image, from the top-left corner
CAMERAS = {
    "lakeland-treetops":   {"name": "Lakeland Dr S at Treetops Blvd", "stream": "011404", "host": "streamingjxn2",
                            "zone": [(0, 0.22), (1, 0.22), (1, 1), (0, 1)]},
    "lakeland-n-treetops": {"name": "Lakeland Dr N at Treetops Blvd", "stream": "011403", "host": "streamingjxn2",
                            "zone": [(0, 1), (1, 1), (1, 0.24), (0.656, 0.24), (0, 0.406)]},
    "jackson-e-fraternity": {"name": "Jackson E at Frtrnty", "stream": "060106", "host": "streamingjxn4",
                             "zone": [(0.094, 0.3125), (0.39, 0.3125), (0.875, 0.73), (1, 0.81), (1, 1), (0.383, 1)]},
    "jackson-w-fraternity": {"name": "Jackson W at Frtrnty", "stream": "060105", "host": "streamingjxn4",
                             "zone": [(0, 0.875), (0.672, 0.29), (0.906, 0.29), (0.78, 0.625), (0.625, 1), (0, 1)]},
}
TILE_ONLY_MIN = 0.30   # vehicles found only in zoomed tiles need more confidence
FLAT_MAX_H = 14        # boxes under this many pixels tall and much wider than tall are road markings
BATCH = 250


# ---------- Supabase ----------
def load_env():
    env_file = Path(__file__).with_name(".env")
    if env_file.exists():
        raw = env_file.read_bytes()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
        for line in text.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip().strip("\ufeff\u200b\u00a0")
                v = v.strip().strip('"').strip("'")
                if not os.environ.get(k):
                    os.environ[k] = v
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        others = [p.name for p in env_file.parent.iterdir() if p.name.lower().startswith(".env")]
        raise SystemExit(
            "Set SUPABASE_URL and SUPABASE_KEY.\n"
            f"  Looked for: {env_file} (exists: {env_file.exists()})\n"
            f"  .env-like files in this folder: {others or 'none'}\n"
            f"  Found URL: {bool(url)}, found KEY: {bool(key)}")
    return url.rstrip("/"), key


def request(url, key, method, path, rows=None, prefer="return=minimal"):
    req = urllib.request.Request(
        f"{url}/rest/v1/{path}",
        data=json.dumps(rows).encode() if rows is not None else None,
        method=method,
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": prefer})
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def upsert(url, key, table, rows, on_conflict):
    request(url, key, "POST", f"{table}?on_conflict={on_conflict}", rows,
            "resolution=merge-duplicates,return=minimal")


# ---------- detection ----------
def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    aa = (a[2] - a[0]) * (a[3] - a[1]); bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter + 1e-9), inter / (min(aa, bb) + 1e-9)


def detect(model, path, tiles=True):
    """Returns ([(x1, y1, x2, y2, conf, class, full_pass, w_px, h_px), ...] with box
    coordinates as 0-1 fractions, and mean brightness). full_pass is True when the
    whole-image pass also found the vehicle."""
    import numpy as np
    from PIL import Image
    img = Image.open(path).convert("RGB"); W, H = img.size
    arr = np.asarray(img)
    found = []

    def run(a):
        r = model(a[:, :, ::-1], conf=DET_FLOOR, classes=list(VEHICLES), imgsz=640, verbose=False)[0]
        return zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist())

    full = [(*b, c, int(k)) for b, c, k in run(arr)]
    found.extend(full)
    if tiles:
        tw, th = W // 2, H // 2
        for y0 in (0, th // 2, th):
            for x0 in (0, tw // 2, tw):
                crop = np.asarray(img.crop((x0, y0, x0 + tw, y0 + th)).resize((tw * 2, th * 2)))
                for b, c, k in run(crop):
                    x1, y1, x2, y2 = x0 + b[0] / 2, y0 + b[1] / 2, x0 + b[2] / 2, y0 + b[3] / 2
                    m = 3  # skip boxes cut off by an inner tile edge; the whole-image pass has those
                    if (x1 - x0 < m and x0 > 0) or (x0 + tw - x2 < m and x0 + tw < W) or \
                       (y1 - y0 < m and y0 > 0) or (y0 + th - y2 < m and y0 + th < H):
                        continue
                    found.append((x1, y1, x2, y2, c, int(k)))
    found.sort(key=lambda d: -d[4])
    keep = []
    for d in found:  # merge duplicates from the overlapping passes
        if all(not (o[0] > 0.45 or o[1] > 0.75) for o in (_overlap(d, k) for k in keep)):
            keep.append(d)
    dets = []
    for d in keep:
        x1, y1, x2, y2, c, k = d
        fp = any(d is f or _overlap(d, f)[0] > 0.3 for f in full)
        dets.append((x1 / W, y1 / H, x2 / W, y2 / H, c, VEHICLES[k], fp, x2 - x1, y2 - y1))
    return dets, float(arr.mean())


def counts_as_vehicle(conf_value, full_pass, w, h, conf):
    if h < FLAT_MAX_H and w / max(h, 1) > 1.7:
        return False
    return conf_value >= (conf if full_pass else max(conf, TILE_ONLY_MIN))


def in_zone(poly, x, y):
    inside = False
    for i in range(len(poly)):
        (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % len(poly)]
        if (ya > y) != (yb > y) and x < (xb - xa) * (y - ya) / (yb - ya + 1e-12) + xa:
            inside = not inside
    return inside


def to_int(v):
    return int(v) if v is not None and str(v).strip() != "" else None


# ---------- main loop ----------
def run_once(out, url, key, model, cam, conf, tiles):
    zone = CAMERAS[cam]["zone"]
    log_path, state = out / "frames.csv", out / ".last_upload"
    last = int(state.read_text()) if state.exists() else 0
    if not log_path.exists():
        print(f"no frames.csv in {out} yet"); return
    with open(log_path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("epoch_ms") or "").isdigit()
                and len(r.get("md5") or "") == 32 and int(r["epoch_ms"]) > last]
    if not rows:
        print("nothing new"); return
    model_tag = MODEL_NAME + ("+tiles" if tiles else "")

    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        frames, dets, counted = [], [], []
        for r in chunk:
            ms = int(r["epoch_ms"])
            counts = {v: to_int(r.get(v)) for v in VEHICLES.values()}
            total = to_int(r.get("total")); brightness = thr = None; tag = MODEL_NAME if total is not None else None
            img = out / r["file"]
            if img.exists():
                found, brightness = detect(model, img, tiles)
                counts = {v: 0 for v in VEHICLES.values()}
                for j, (x1, y1, x2, y2, p, name, fp, wpx, hpx) in enumerate(found):
                    inside = in_zone(zone, (x1 + x2) / 2, y2)
                    if inside and counts_as_vehicle(p, fp, wpx, hpx, conf):
                        counts[name] += 1
                    dets.append({"camera": cam, "epoch_ms": ms, "idx": j, "class": name,
                                 "conf": round(p, 4), "x1": round(x1, 4), "y1": round(y1, 4),
                                 "x2": round(x2, 4), "y2": round(y2, 4), "in_zone": inside,
                                 "full_pass": fp})
                total, thr, tag = sum(counts.values()), conf, model_tag
                brightness = round(brightness, 1)
                counted.append(ms)
            frames.append({"camera": cam, "epoch_ms": ms, "captured_at": r["timestamp_local"],
                           "file": r["file"], "bytes": to_int(r.get("bytes")), "md5": r["md5"],
                           **counts, "total": total, "model": tag,
                           "brightness": brightness, "conf_threshold": thr})

        upsert(url, key, "traffic_frames", frames, "camera,epoch_ms")
        for k in range(0, len(counted), 200):
            ids = ",".join(map(str, counted[k:k + 200]))
            request(url, key, "DELETE", f"traffic_detections?camera=eq.{cam}&epoch_ms=in.({ids})")
        for k in range(0, len(dets), 1000):
            upsert(url, key, "traffic_detections", dets[k:k + 1000], "camera,epoch_ms,idx")
        state.write_text(str(frames[-1]["epoch_ms"]))
        print(f"{time.strftime('%H:%M')} uploaded {i + len(chunk)}/{len(rows)} frames "
              f"({len(dets)} detections in this batch)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default="lakeland-treetops", choices=list(CAMERAS))
    ap.add_argument("--out", default="cam_data", help="folder capture.py writes to")
    ap.add_argument("--conf", type=float, default=0.20, help="confidence needed to count a vehicle")
    ap.add_argument("--no-tiles", action="store_true", help="faster, but misses more distant vehicles")
    ap.add_argument("--every", type=float, default=0, help="minutes between runs; 0 = once")
    a = ap.parse_args()

    url, key = load_env()
    from ultralytics import YOLO
    model = YOLO(MODEL_NAME)
    print(f"Counting {CAMERAS[a.camera]['name']} from {a.out}")
    while True:
        try:
            run_once(Path(a.out), url, key, model, a.camera, a.conf, not a.no_tiles)
        except Exception as e:
            print(f"upload failed: {e}")
        if not a.every:
            break
        time.sleep(a.every * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("stopped")
