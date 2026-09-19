#!/usr/bin/env python3
"""Count vehicles on frames saved by capture.py and upload them to Supabase.

Vehicles are found with YOLO11 medium at 960 pixels (optionally plus zoomed
tiles). Only vehicles whose bottom-center falls inside the camera's counting
zone (the near and middle stretch of road) count toward the totals. Every
detection down to 0.10 confidence is still stored in traffic_detections.

Camera moves: each camera's reference view (Supabase table camera_refs) is the
median of several daylight frames, which leaves the static scene and clears the
traffic. Later frames are compared with it; small pans or zooms move the counting
zone to follow, and a big change is flagged (view_status = changed) and counted
with a general lower-frame zone. A reference that keeps checking out as unmoved is
re-based every few hours so it follows the changing light.

Safe to re-run and safe while capture.py is running: it resumes after the last
uploaded frame (cam folder/.last_upload) and overwrites instead of duplicating.
Each image is deleted once its counts are safely in Supabase (use --keep-images
to keep them); frames whose image is gone are never re-uploaded, so recounting
can't blank out existing data.
Needs SUPABASE_URL and SUPABASE_KEY in a .env file next to this script.

  Lakeland Dr S (home):  python upload.py --every 60
  Lakeland Dr N:         python upload.py --camera lakeland-n-treetops --out cam_011403 --every 15
"""
import argparse, csv, json, os, time, urllib.request
from pathlib import Path

VEHICLES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
NAMES = {0: "person", **VEHICLES}  # people are counted separately from vehicles
PERSON_MIN = 0.25  # confidence needed to count a person (anywhere in the frame)
MODEL_NAME = "yolo11m.pt"
# Counts that came from capture.py --detect (legacy) were made by this model, not MODEL_NAME
CAPTURE_MODEL = "yolo11n.pt"
IMGSZ = 960
FALLBACK_ZONE = [(0, 0.25), (1, 0.25), (1, 1), (0, 1)]  # used when a camera view no longer matches
DET_FLOOR = 0.10
REF_FRAMES = 9          # frames medianed together to build a reference view
REF_MIN_FRAMES = 5      # below this the median cannot clear the traffic, so wait for a fuller batch
REF_MAX_AGE_H = 6       # routine freshening: re-base a reference that still matches, this often
REF_RETRY_H = 1         # recovery: how long a reference that has STOPPED matching may persist
# Counting zones as (x, y) fractions of the image, from the top-left corner
CAMERAS = {
    "lakeland-treetops":   {"name": "Lakeland Dr S at Treetops Blvd", "stream": "011404", "host": "streamingjxn2",
                            "zone": [(0, 0.22), (1, 0.22), (1, 1), (0, 1)]},
    "lakeland-n-treetops": {"name": "Lakeland Dr N at Treetops Blvd", "stream": "011403", "host": "streamingjxn2",
                            "zone": [(0, 1), (1, 1), (1, 0.24), (0.656, 0.24), (0, 0.406)]},
    "jackson-e-fraternity": {"name": "Jackson E at Frtrnty", "stream": "060106", "host": "streamingjxn4",
                             "zone": [(0.109, 0.25), (0.305, 0.25), (1, 0.80), (1, 1), (0.383, 1)]},
    "jackson-w-fraternity": {"name": "Jackson W at Frtrnty", "stream": "060105", "host": "streamingjxn4",
                             "zone": [(0, 0.875), (0.672, 0.29), (0.906, 0.29), (0.78, 0.625), (0.625, 1), (0, 1)]},
    "lakeland-n-airport":   {"name": "Lakeland Dr N at Airport Rd", "stream": "010102", "host": "streamingjxn2",
                             "zone": [(0.344, 0.23), (0.734, 0.23), (1, 0.427), (1, 1), (0, 1), (0, 0.69), (0.36, 0.545), (0.347, 0.23)]},
    "university-w-lamar":   {"name": "University W at Lamar", "stream": "060204", "host": "streamingjxn4",
                             "zone": [(0.195, 0.177), (0.258, 0.177), (0.469, 0.354), (0.688, 0.552), (1, 0.583), (1, 1),
                                      (0.336, 1), (0.367, 0.688), (0.3125, 0.479)]},
}
# Extra cameras (and zones) can be listed in cameras.json next to this script
_extra = Path(__file__).with_name("cameras.json")
if _extra.exists():
    CAMERAS.update({k: {**v, "zone": [tuple(p) for p in v["zone"]]}
                    for k, v in json.loads(_extra.read_text()).get("cameras", {}).items()})
TILE_ONLY_MIN = 0.30   # vehicles found only in zoomed tiles need more confidence
FLAT_MAX_H = 14        # boxes under this many pixels tall and much wider than tall are road markings
EDGE_MIN = 0.35        # low-confidence boxes touching two image edges are usually pavement or shadow
BATCH = 250
SOURCE = os.environ.get("TRAFFIC_SOURCE", "local")  # "github" on the scheduled samples


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


def get_json(url, key, path):
    req = urllib.request.Request(f"{url}/rest/v1/{path}", headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"[]")


def upsert(url, key, table, rows, on_conflict):
    request(url, key, "POST", f"{table}?on_conflict={on_conflict}", rows,
            "resolution=merge-duplicates,return=minimal")


# ---------- detection ----------
def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    aa = (a[2] - a[0]) * (a[3] - a[1]); bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter + 1e-9), inter / (min(aa, bb) + 1e-9)


def detect(model, path, tiles=False):
    """Returns ([(x1, y1, x2, y2, conf, class, full_pass, w_px, h_px, edges), ...] with box
    coordinates as 0-1 fractions, and mean brightness). full_pass is True when the
    whole-image pass also found the object."""
    import numpy as np
    from PIL import Image
    img = Image.open(path).convert("RGB"); W, H = img.size
    arr = np.asarray(img)
    found = []

    def run(a):
        r = model(a[:, :, ::-1], conf=DET_FLOOR, classes=list(NAMES), imgsz=size, verbose=False)[0]
        return zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist())

    size = IMGSZ
    full = [(*b, c, int(k)) for b, c, k in run(arr)]
    size = 640
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
        edges = (x1 < 2) + (y1 < 2) + (x2 > W - 2) + (y2 > H - 2)
        dets.append((x1 / W, y1 / H, x2 / W, y2 / H, c, NAMES[k], fp, x2 - x1, y2 - y1, edges))
    return dets, float(arr.mean())


def counts_as_vehicle(conf_value, full_pass, w, h, conf, edges=0):
    if h < FLAT_MAX_H and w / max(h, 1) > 1.7:
        return False
    if edges >= 2 and conf_value < EDGE_MIN:
        return False
    return conf_value >= (conf if full_pass else max(conf, TILE_ONLY_MIN))


def in_zone(poly, x, y):
    inside = False
    for i in range(len(poly)):
        (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % len(poly)]
        if (ya > y) != (yb > y) and x < (xb - xa) * (y - ya) / (yb - ya + 1e-12) + xa:
            inside = not inside
    return inside


# ---------- camera-move detection ----------
_sift = None


def _small_gray(path):
    import cv2
    return cv2.cvtColor(cv2.resize(cv2.imread(str(path)), (320, 240), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)


def _median_gray(paths):
    """Median of several frames. Cars and headlight glare land in different places
    from frame to frame and drop out; the static scene survives."""
    import cv2, numpy as np
    return np.median(np.stack([_small_gray(p) for p in paths]), 0).astype(np.uint8)


def _ref_pool(out, chunk, want):
    """Evenly spaced frames from this batch to build a reference from, newest light
    included. Infrared frames are left out so a reference is never half infrared."""
    paths = [p for p in (out / r["file"] for r in chunk) if p.exists()]
    if len(paths) > want:
        step = len(paths) / want
        paths = [paths[int(k * step)] for k in range(want)]
    return [p for p in paths if not _is_night(p)]


def _age_hours(stamp):
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0  # unreadable timestamp: leave the reference alone rather than churn it
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600


def _is_night(path):
    import cv2, numpy as np
    hsv = cv2.cvtColor(cv2.resize(cv2.imread(str(path)), (160, 120)), cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 1])) < 12  # infrared frames have almost no color


def _features(g):
    import cv2, numpy as np
    global _sift
    _sift = _sift or cv2.SIFT_create(1500)
    g = cv2.createCLAHE(2.0, (8, 8)).apply(g)
    m = np.zeros_like(g); m[10:212, :] = 255; m[g < 25] = 0  # skip the name overlay and dark housing
    return _sift.detectAndCompute(g, m)


def load_ref(url, key, cam):
    import cv2, numpy as np, base64
    rows = get_json(url, key, f"camera_refs?camera=eq.{cam}&select=ref_jpg,zone,created_at")
    if not rows:
        return None
    g = cv2.imdecode(np.frombuffer(base64.b64decode(rows[0]["ref_jpg"]), np.uint8), cv2.IMREAD_GRAYSCALE)
    return {"feats": _features(g), "zone": [tuple(p) for p in rows[0]["zone"]],
            "age_h": _age_hours(rows[0]["created_at"])}


def save_ref(url, key, cam, paths, zone):
    """Store the median of several frames as the reference view.

    A single frame was not enough to match against. On a busy night camera most of
    its strong features are cars and headlight bloom, which are gone by the next
    frame, so only about 20 usable matches survived and the homography check sat on
    its own threshold: identical, unmoved views scored 14 to 16 inliers against a
    cutoff of 15 and flipped between 'same' and 'changed' frame to frame. Medianing
    the traffic out leaves the poles, lane markings and crosswalk, which match at 70
    to 300 inliers, so the check clears its thresholds by a wide margin and only a
    real pan or zoom brings it back down.
    """
    import cv2, numpy as np, base64
    from datetime import datetime, timezone
    ok, buf = cv2.imencode(".jpg", _median_gray(paths), [cv2.IMWRITE_JPEG_QUALITY, 75])
    raw = buf.tobytes()
    upsert(url, key, "camera_refs", [{"camera": cam, "ref_jpg": base64.b64encode(raw).decode(),
                                      "zone": [list(p) for p in zone],
                                      "created_at": datetime.now(timezone.utc).isoformat()}], "camera")
    # Match on the stored picture, not the one in memory, so a reference behaves the
    # same in the run that wrote it as in every run that loads it back.
    g = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    return {"feats": _features(g), "zone": zone, "age_h": 0.0}


def check_view(ref, path):
    """Returns (status, zone for this frame)."""
    import cv2, numpy as np
    ka, da = ref["feats"]; kb, db = _features(_small_gray(path))
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return "changed", FALLBACK_ZONE
    pairs = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
    if len(good) < 12:
        return "changed", FALLBACK_ZONE
    Hm, inl = cv2.findHomography(np.float32([ka[m.queryIdx].pt for m in good]),
                                 np.float32([kb[m.trainIdx].pt for m in good]), cv2.RANSAC, 3.0)
    n_in = int(inl.sum()) if inl is not None else 0
    if Hm is None or n_in < 15 or n_in < 0.4 * len(good):
        return "changed", FALLBACK_ZONE
    scale = float(np.sqrt(abs(np.linalg.det(Hm[:2, :2]))))
    if not 0.6 < scale < 1.6:
        return "changed", FALLBACK_ZONE
    pts = np.float32([[x * 320, y * 240] for x, y in ref["zone"]]).reshape(-1, 1, 2)
    moved = cv2.perspectiveTransform(pts, Hm).reshape(-1, 2)
    shift = float(np.abs(moved - pts.reshape(-1, 2)).mean())
    if shift < 4:
        return "same", ref["zone"]
    return "shifted", [(min(max(x / 320, 0), 1), min(max(y / 240, 0), 1)) for x, y in moved]


def to_int(v):
    return int(v) if v is not None and str(v).strip() != "" else None


# ---------- main loop ----------
def run_once(out, url, key, model, cam, conf, tiles, check_every=20, keep_images=False):
    base_zone = CAMERAS[cam]["zone"]
    try:
        ref = load_ref(url, key, cam)
    except Exception as e:
        print(f"could not load reference view: {e}"); ref = None
    view, zone, since_check = None, base_zone, check_every
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
        # Only decoded when a reference is missing or due to be re-based; the rest of
        # the time reading these frames twice would be wasted work.
        pool = (_ref_pool(out, chunk, REF_FRAMES)
                if ref is None or ref["age_h"] >= min(REF_RETRY_H, REF_MAX_AGE_H) else [])
        for r in chunk:
            ms = int(r["epoch_ms"])
            counts = {v: to_int(r.get(v)) for v in VEHICLES.values()}
            total = to_int(r.get("total")); brightness = thr = people = None; tag = CAPTURE_MODEL if total is not None else None
            img = out / r["file"]
            if not img.exists() and total is None:
                continue  # image already deleted and nothing to add: keep what's in Supabase
            if img.exists():
                if _is_night(img):
                    view, zone = "night", (ref["zone"] if ref else base_zone)
                else:
                    if ref is None:
                        if len(pool) >= REF_MIN_FRAMES:
                            ref = save_ref(url, key, cam, pool, base_zone)
                            view, zone = "same", base_zone
                            print(f"saved reference view from {len(pool)} frames")
                        else:
                            # Too few frames to median; count with the drawn zone and
                            # set the reference on a fuller batch rather than from one frame.
                            view, zone = None, base_zone
                    elif view in (None, "night") or since_check >= check_every:
                        view, zone = check_view(ref, img); since_check = 0
                        if len(pool) >= REF_MIN_FRAMES:
                            if view == "same" and ref["age_h"] >= REF_MAX_AGE_H:
                                # Verified unmoved, so re-base on current light and keep
                                # the zone that was just confirmed to fit.
                                ref = save_ref(url, key, cam, pool, ref["zone"])
                                print(f"refreshed reference view from {len(pool)} frames")
                            elif view == "changed" and ref["age_h"] >= REF_RETRY_H:
                                # SIFT cannot match across a change in the light however
                                # good the reference is, so a reference stops matching a few
                                # hours after it was built. Rebuild from what the camera sees
                                # now, with the zone from the code -- the same recovery a
                                # person would do by deleting the camera_refs row. This frame
                                # still counts as 'changed'; the next check passes.
                                # Measured Sep 19: references built at 10:09 matched 0 of 9
                                # frames by 15:00 while a fresh median of those same frames
                                # matched 9 of 9. At a 6 h threshold three cameras spent that
                                # whole afternoon on the fallback zone, so recovery gets its
                                # own, much shorter clock. The cost is that a camera that
                                # really was re-aimed reads 'changed' for an hour rather than
                                # six before it becomes its own reference; the fallback zone
                                # inflates counts every day, a re-aim is rare.
                                ref = save_ref(url, key, cam, pool, base_zone)
                                print(f"reference no longer matched; rebuilt from {len(pool)} frames")
                    since_check += 1
                found, brightness = detect(model, img, tiles)
                counts = {v: 0 for v in VEHICLES.values()}; people = 0
                for j, (x1, y1, x2, y2, p, name, fp, wpx, hpx, edges) in enumerate(found):
                    inside = in_zone(zone, (x1 + x2) / 2, y2)
                    if name == "person":
                        if p >= PERSON_MIN and y2 < 0.93:  # anywhere except the name overlay
                            people += 1
                    elif inside and counts_as_vehicle(p, fp, wpx, hpx, conf, edges):
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
                           **counts, "total": total, "people": people, "model": tag,
                           "brightness": brightness, "conf_threshold": thr,
                           "view_status": view if img.exists() else None,
                           "source": SOURCE})

        if not frames:
            continue
        upsert(url, key, "traffic_frames", frames, "camera,epoch_ms")
        for k in range(0, len(counted), 200):
            ids = ",".join(map(str, counted[k:k + 200]))
            request(url, key, "DELETE", f"traffic_detections?camera=eq.{cam}&epoch_ms=in.({ids})")
        for k in range(0, len(dets), 1000):
            upsert(url, key, "traffic_detections", dets[k:k + 1000], "camera,epoch_ms,idx")
        state.write_text(str(frames[-1]["epoch_ms"]))
        if not keep_images:  # counts and detections are saved; the pictures aren't needed
            for fr in frames:
                try:
                    (out / fr["file"]).unlink(missing_ok=True)
                except OSError:
                    pass
        print(f"{time.strftime('%H:%M')} uploaded {i + len(chunk)}/{len(rows)} frames "
              f"({len(dets)} detections in this batch)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default="lakeland-treetops", choices=list(CAMERAS))
    ap.add_argument("--out", default="cam_data", help="folder capture.py writes to")
    ap.add_argument("--conf", type=float, default=0.20, help="confidence needed to count a vehicle")
    ap.add_argument("--tiles", action="store_true", help="also check zoomed-in tiles (slower; the medium model rarely needs it)")
    ap.add_argument("--every", type=float, default=0, help="minutes between runs; 0 = once")
    ap.add_argument("--keep-images", action="store_true", help="keep frame images after uploading")
    a = ap.parse_args()

    url, key = load_env()
    from ultralytics import YOLO
    model = YOLO(MODEL_NAME)
    print(f"Counting {CAMERAS[a.camera]['name']} from {a.out}")
    while True:
        try:
            run_once(Path(a.out), url, key, model, a.camera, a.conf, a.tiles, keep_images=a.keep_images)
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
