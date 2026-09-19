#!/usr/bin/env python3
"""Count vehicles passing counting lines in a short sample of a live traffic video.

Reads the camera's HLS (HTTP Live Streaming) feed directly for a few minutes,
using about 10 frames per second, finds moving vehicles by background
subtraction (no neural network needed, so it is fast), follows each one from
frame to frame, and counts it once when it crosses a line drawn across the road.
Results go to the Supabase table traffic_video_counts, one row per line and
direction.

Cameras and lines live in cameras.json under "video". Line coordinates are
pixels in the full-size video (1280x720 for MDOT); direction "toward" means
moving toward the camera side of the line. Non-MDOT feeds set "video_url";
optional per-camera tuning: min_area, min_w, min_h, max_jump, view_check.

  python video.py --camera university-e-ms7 --seconds 180
"""
import argparse, datetime as dt, json, tempfile, time, urllib.request
from pathlib import Path

import upload  # reuses Supabase helpers and camera-move detection


def load_video_cameras():
    cfg = Path(__file__).with_name("cameras.json")
    return json.loads(cfg.read_text()).get("video", {}) if cfg.exists() else {}


def snapshot(cam, path):
    if cam.get("video_url"):  # non-MDOT feeds (e.g. Louisiana DOTD): take a frame from the video itself
        import cv2
        cap = cv2.VideoCapture(cam["video_url"], cv2.CAP_FFMPEG)
        ok, frame = cap.read(); cap.release()
        if not ok:
            raise RuntimeError("could not read a frame from the video stream")
        cv2.imwrite(str(path), cv2.resize(frame, (640, 480)))
        return
    url = (f"https://{cam['host']}.mdottraffic.com/thumbnail?application=rtplive&streamname={cam['stream']}.stream"
           f"&size=640x480&format=jpg&fitmode=stretch&t={int(time.time() * 1000)}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        Path(path).write_bytes(r.read())


def _side(p, a, b):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _cross(p, q, a, b):
    """+1 / -1 when the step p->q crosses segment ab (sign = direction), else 0."""
    s1, s2 = _side(p, a, b), _side(q, a, b)
    if s1 * s2 >= 0:
        return 0
    return (1 if s1 < 0 else -1) if _side(a, p, q) * _side(b, p, q) < 0 else 0


def count_crossings(cam, seconds):
    import cv2
    ox, oy = cam["crop"][0], cam["crop"][1]
    lines = {name: ((l[0][0] - ox, l[0][1] - oy), (l[1][0] - ox, l[1][1] - oy)) for name, l in cam["lines"].items()}
    counts = {name: {"toward": 0, "away": 0} for name in lines}
    url = cam.get("video_url") or f"https://{cam['host']}.mdottraffic.com/rtplive/{cam['stream']}.stream/playlist.m3u8"
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise RuntimeError("could not open the video stream")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(fps / 10))              # analyse about 10 frames per second
    x0, y0, cw, ch = cam["crop"]
    raw_left = int(seconds * fps) + 30 * step   # plus the 3-second warm-up
    bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=25, detectShadows=True)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    tracks, next_id, frame_no = {}, 0, 0
    min_area, max_jump = cam.get("min_area", 250), cam.get("max_jump", 45)
    min_w, min_h = cam.get("min_w", 12), cam.get("min_h", 8)
    raw = 0
    while raw < raw_left:
        ok, full = cap.read()
        if not ok:
            break
        raw += 1
        if raw % step:
            continue
        img = full[y0:y0 + ch, x0:x0 + cw]
        frame_no += 1
        fg = bg.apply(img)
        if frame_no < 30:  # first 3 seconds: learn the empty road
            continue
        fg = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)[1]  # drop shadows
        fg = cv2.dilate(cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel), kernel, iterations=2)
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w * h >= min_area and w >= min_w and h >= min_h:
                blobs.append((x + w / 2, y + h / 2))
        used, updated = set(), {}
        for tid, tr in tracks.items():  # match each track to the nearest blob near its predicted spot
            px, py = tr["p"][0] + tr["v"][0], tr["p"][1] + tr["v"][1]
            best, best_d = None, max_jump
            for j, b in enumerate(blobs):
                if j not in used:
                    d = ((b[0] - px) ** 2 + (b[1] - py) ** 2) ** 0.5
                    if d < best_d:
                        best, best_d = j, d
            if best is not None:
                used.add(best); p = blobs[best]
                for name, (a, b) in lines.items():
                    direction = _cross(tr["p"], p, a, b)
                    if direction and name not in tr["done"]:
                        tr["done"].add(name)
                        counts[name]["toward" if direction > 0 else "away"] += 1
                v = (0.5 * tr["v"][0] + 0.5 * (p[0] - tr["p"][0]), 0.5 * tr["v"][1] + 0.5 * (p[1] - tr["p"][1]))
                updated[tid] = {"p": p, "v": v, "miss": 0, "done": tr["done"]}
            elif tr["miss"] < 5:  # keep coasting briefly through occlusions (poles, trees)
                updated[tid] = {**tr, "p": (tr["p"][0] + tr["v"][0], tr["p"][1] + tr["v"][1]), "miss": tr["miss"] + 1}
        for j, b in enumerate(blobs):
            if j not in used:
                next_id += 1
                updated[next_id] = {"p": b, "v": (0, 0), "miss": 0, "done": set()}
        tracks = updated
    cap.release()
    analysed = max(0.0, (frame_no - 30) * step / fps)
    return counts, analysed


FULL_ZONE = [(0, 0), (1, 0), (1, 1), (0, 1)]  # video cameras count by line, not by zone


def ref_snaps(cam, tmp, gap=2.0):
    """Several spaced snapshots, so the reference view can be medianed free of traffic.
    Only taken when a reference is being set or re-based, which is rare."""
    paths = []
    for i in range(upload.REF_FRAMES):
        if i:
            time.sleep(gap)
        path = tmp / f"ref{i}.jpg"
        try:
            snapshot(cam, path)
        except Exception as e:
            print(f"reference snapshot {i} failed: {e}"); continue
        paths.append(path)
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", required=True)
    ap.add_argument("--seconds", type=float, default=180)
    a = ap.parse_args()
    cams = load_video_cameras()
    cam = cams[a.camera]
    url, key = upload.load_env()
    started = dt.datetime.now(dt.timezone.utc)

    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "snap.jpg"
        view = None
        try:
            if cam.get("view_check") is False:
                raise RuntimeError("view check turned off for this camera")
            snapshot(cam, snap)
            if upload._is_night(snap):
                view = "night"
            else:
                ref_key = f"video:{a.camera}"
                ref = upload.load_ref(url, key, ref_key)
                if ref is None:
                    snaps = ref_snaps(cam, Path(tmp))
                    if len(snaps) >= upload.REF_MIN_FRAMES:
                        upload.save_ref(url, key, ref_key, snaps, FULL_ZONE); view = "same"
                    else:
                        print("not enough snapshots to set a reference view; counting anyway")
                else:
                    view, _ = upload.check_view(ref, snap)
                    # Re-base an old reference, whether it still matches or not: one left
                    # to go stale is what had this camera skipping every run for an
                    # afternoon, and one that stopped matching cannot recover on its own.
                    if ref["age_h"] >= upload.REF_MAX_AGE_H and view in ("same", "changed"):
                        snaps = ref_snaps(cam, Path(tmp))
                        if len(snaps) >= upload.REF_MIN_FRAMES:
                            # A confirmed view keeps its zone; one that no longer matches
                            # falls back to the zone from the code.
                            upload.save_ref(url, key, ref_key, snaps,
                                            ref["zone"] if view == "same" else FULL_ZONE)
                            print("refreshed reference view" if view == "same"
                                  else "reference no longer matched; rebuilt")
        except Exception as e:
            print(f"view check skipped: {e}")

        rows = []
        if view in ("changed", "shifted"):
            # counting lines only fit the original view; record the gap instead of wrong numbers
            for name in cam["lines"]:
                for d in ("toward", "away"):
                    rows.append({"camera": a.camera, "started_at": started.isoformat(), "seconds": 0,
                                 "line": name, "direction": d, "vehicles": None, "view_status": view,
                                 "source": upload.SOURCE})
            print(f"camera view {view}; not counting this run")
        else:
            counts, seconds = count_crossings(cam, a.seconds)
            for name, dirs in counts.items():
                for d, n in dirs.items():
                    rows.append({"camera": a.camera, "started_at": started.isoformat(), "seconds": round(seconds, 1),
                                 "line": name, "direction": d, "vehicles": n, "view_status": view,
                                 "source": upload.SOURCE})
            print(f"{cam['name']}: {seconds:.0f}s analysed, {counts}")
        upload.upsert(url, key, "traffic_video_counts", rows, "camera,started_at,line,direction")


if __name__ == "__main__":
    main()
