"""
Watermark-removal processing pipeline.

Targets the *visible* Gemini / Veo sparkle logo (bottom-right corner,
sometimes tiled). The invisible SynthID watermark is embedded in the
pixels by Google and is NOT removed by this (or any pixel-editing)
pipeline -- see README.md.

Strategy
--------
Images
  1. Geometry prior: Gemini stamps a square logo at the bottom-right.
     Publicly documented sizing: 48x48 with a 32px margin on smaller
     images, 96x96 with a 64px margin on images whose larger side
     exceeds 1024px. That box is only a *prior* for where to look.
  2. Detection: inside the bottom-right search region, find the sparkle
     itself -- bright, low-saturation pixels (the logo is white / light
     grey and semi-transparent) -- then keep contours near the prior box.
  3. Removal: OpenCV inpainting (Telea) over the detected mask, with a
     feathered edge blend so the patch has no hard seam. If a user
     supplies a clean alpha map of the logo (logo_alpha.png), true
     reverse alpha blending is used instead:
         original = (blended - alpha * logo) / (1 - alpha)

Video
  One mask is computed from the temporal median of the first frames
  (the logo is static while the scene moves) and reused for *every*
  frame -- that is what prevents flicker. Frames are piped through
  ffmpeg so the output keeps the original resolution, frame rate,
  video bitrate target and the untouched audio track (stream copy).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------- images


def _logo_prior_box(w: int, h: int) -> tuple[int, int, int, int]:
    """(x, y, size, margin) of the expected Gemini logo, bottom-right."""
    if max(w, h) > 1024:
        size, margin = 96, 64
    else:
        size, margin = 48, 32
    return w - margin - size, h - margin - size, size, margin


def detect_mask(bgr: np.ndarray) -> np.ndarray:
    """Return a uint8 mask (255 = watermark) for one BGR frame/image.

    Detection is *relative*, not an absolute brightness threshold: the
    sparkle is a white overlay, so inside the small search window around
    the prior box it is much brighter and less saturated than the local
    background (estimated with a large median blur). An absolute
    threshold would false-positive on any bright corner (sky, paper).
    """
    h, w = bgr.shape[:2]
    px, py, size, _ = _logo_prior_box(w, h)

    pad = max(8, size // 4)
    sx0, sy0 = max(0, px - pad), max(0, py - pad)
    sx1, sy1 = min(w, px + size + pad), min(h, py + size + pad)
    roi = bgr[sy0:sy1, sx0:sx1]

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).astype(np.int16)
    bg = cv2.medianBlur(roi, 25)
    hsv_bg = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV).astype(np.int16)

    lum_gain = hsv[:, :, 2] - hsv_bg[:, :, 2]      # watermark lifts value
    cand = ((lum_gain > 20) & (hsv[:, :, 2] > 150) &
            (hsv[:, :, 1] < 90)).astype(np.uint8) * 255
    cand = cv2.morphologyEx(cand, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    kept = np.zeros_like(cand)
    contours, _ = cv2.findContours(cand, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        if cv2.contourArea(c) >= 20:
            cv2.drawContours(kept, [c], -1, 255, -1)

    # Bridge gaps: the logo is one compact mark, but its thin arms can
    # fragment in detection -- close gaps up to ~1/3 of the logo size.
    k = max(5, size // 3)
    kept = cv2.morphologyEx(kept, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))

    # Fill holes: where the median estimate sits *inside* the logo (its
    # centre), the relative test can miss pixels surrounded by detected
    # ones -- flood-fill the background and take whatever is left.
    if (kept > 0).any():
        inv = cv2.bitwise_not(kept)
        ff = inv.copy()
        ffmask = np.zeros((inv.shape[0] + 2, inv.shape[1] + 2), np.uint8)
        cv2.floodFill(ff, ffmask, (0, 0), 128)
        holes = (ff == 255).astype(np.uint8) * 255
        kept = cv2.bitwise_or(kept, holes)

    mask = np.zeros((h, w), np.uint8)
    if (kept > 0).sum() >= 30:
        # The fragments belong to one compact logo: mask the filled
        # bounding box of the fragments (thin-arm centres that evade
        # the relative test are inside it) rather than the fragments
        # alone, which would leave the logo's middle behind.
        ys, xs = np.where(kept > 0)
        x0, x1 = max(0, xs.min() - 3), min(w, xs.max() + 4)
        y0, y1 = max(0, ys.min() - 3), min(h, ys.max() + 4)
        cv2.rectangle(mask, (sx0 + x0, sy0 + y0), (sx0 + x1, sy0 + y1), 255, -1)
    else:  # detection failed -> fall back to the prior box
        cv2.rectangle(mask, (px, py), (px + size, py + size), 255, -1)

    # The semi-transparent halo extends a couple of px past the bright core.
    return cv2.dilate(mask, np.ones((5, 5), np.uint8))


def reverse_alpha_blend(bgr: np.ndarray, alpha: np.ndarray,
                        logo_rgb=(255, 255, 255)) -> np.ndarray:
    """Exact inverse of alpha compositing, given the logo's alpha map."""
    a = (alpha.astype(np.float32) / 255.0)[..., None]
    a = np.clip(a, 0, 0.95)
    logo = np.array(logo_rgb[::-1], np.float32)  # to BGR
    out = (bgr.astype(np.float32) - a * logo) / (1.0 - a)
    return np.clip(out, 0, 255).astype(np.uint8)


def remove_watermark_image(bgr: np.ndarray,
                           alpha_map: np.ndarray | None = None) -> np.ndarray:
    """Remove the visible corner logo from a single BGR image/frame."""
    mask = detect_mask(bgr)
    if alpha_map is not None:
        if alpha_map.shape[:2] != bgr.shape[:2]:
            alpha_map = cv2.resize(alpha_map, (bgr.shape[1], bgr.shape[0]))
        cleaned = reverse_alpha_blend(bgr, alpha_map)
        m = (mask > 0)[..., None]
        return np.where(m, cleaned, bgr)

    inpainted = cv2.inpaint(bgr, mask, 3, cv2.INPAINT_TELEA)

    # Feathered composite: full-strength inside, soft 3px edge.
    soft = cv2.GaussianBlur(mask, (0, 0), 1.2).astype(np.float32) / 255.0
    soft = soft[..., None]
    out = inpainted.astype(np.float32) * soft + bgr.astype(np.float32) * (1 - soft)
    return out.astype(np.uint8)


def process_image_file(src: Path, dst: Path,
                       alpha_map: np.ndarray | None = None) -> dict:
    img = cv2.imread(str(src), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Could not decode image: {src.name}")
    has_alpha = img.ndim == 3 and img.shape[2] == 4
    bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR) if has_alpha else img
    cleaned = remove_watermark_image(bgr, alpha_map)
    if has_alpha:
        cleaned = cv2.cvtColor(cleaned, cv2.COLOR_BGR2BGRA)
        cleaned[:, :, 3] = img[:, :, 3]
    if not cv2.imwrite(str(dst), cleaned):
        raise RuntimeError(f"Could not write image: {dst.name}")
    return {"width": int(cleaned.shape[1]), "height": int(cleaned.shape[0])}


# ----------------------------------------------------------------- video


def probe_video(src: Path) -> dict:
    """Container/stream facts of the source video, via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(src)],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = v["avg_frame_rate"].split("/")
    fps = float(num) / max(float(den), 1.0)
    return {
        "width": int(v["width"]), "height": int(v["height"]),
        "fps": fps or 30.0,
        "bitrate": int(v.get("bit_rate") or
                       info.get("format", {}).get("bit_rate") or 8_000_000),
        "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
        "codec": v["codec_name"],
    }


def _video_mask(src: Path) -> np.ndarray:
    """One temporally-stable mask from the median of the first frames."""
    cap = cv2.VideoCapture(str(src))
    frames = []
    for _ in range(15):
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    if not frames:
        raise ValueError("Could not read any frame from the video")
    median = np.median(np.stack(frames), axis=0).astype(np.uint8)
    mask = detect_mask(median)
    for f in frames[::5]:  # union a few per-frame detections for safety
        mask = cv2.bitwise_or(mask, detect_mask(f))
    return mask


def process_video_file(src: Path, dst: Path) -> dict:
    meta = probe_video(src)
    w, h, fps, bitrate = meta["width"], meta["height"], meta["fps"], meta["bitrate"]
    mask = _video_mask(src)
    soft = (cv2.GaussianBlur(mask, (0, 0), 1.2).astype(np.float32) / 255.0)[..., None]

    is_webm = src.suffix.lower() == ".webm"
    vcodec = ["-c:v", "libvpx-vp9", "-b:v", str(bitrate)] if is_webm else \
             ["-c:v", "libx264", "-b:v", str(bitrate), "-pix_fmt", "yuv420p"]

    with tempfile.TemporaryDirectory() as td:
        silent = Path(td) / f"silent{src.suffix}"
        enc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
             *vcodec, str(silent)],
            stdin=subprocess.PIPE)

        cap = cv2.VideoCapture(str(src))
        n = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            fixed = cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)
            out = (fixed.astype(np.float32) * soft +
                   frame.astype(np.float32) * (1 - soft)).astype(np.uint8)
            enc.stdin.write(out.tobytes())
            n += 1
        cap.release()
        enc.stdin.close()
        enc.wait()
        if enc.returncode != 0:
            raise RuntimeError("ffmpeg video encode failed")

        if meta["has_audio"]:  # mux the original audio back, untouched
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error",
                 "-i", str(silent), "-i", str(src),
                 "-map", "0:v:0", "-map", "1:a:0",
                 "-c", "copy", "-shortest", str(dst)],
                check=True)
        else:
            silent.replace(dst)
    return {**meta, "frames": n}
