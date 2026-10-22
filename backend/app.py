"""
Backend server for the Gemini / Veo visible-watermark remover clone.

Run:
    pip install -r requirements.txt      # plus a system ffmpeg
    uvicorn app:app --host 0.0.0.0 --port 8000
Then open http://localhost:8000  (the frontend is served statically).

Limits (the original site's headline specs -- env-overridable):
    MAX_IMAGES       = 10 files per request
    MAX_IMAGE_MB     = 20 MB per image
    MAX_VIDEO_MB     = 50 MB per video (single video per request)
Only the *visible* logo is processed; the invisible SynthID watermark
is embedded in the pixels by Google and is not removed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from processing import process_image_file, process_video_file

MAX_IMAGES = int(os.getenv("MAX_IMAGES", "10"))
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "20"))
MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "50"))

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Gemini Watermark Remover (clone)")


@app.get("/api/health")
def health():
    return {"ok": True, "max_images": MAX_IMAGES,
            "max_image_mb": MAX_IMAGE_MB, "max_video_mb": MAX_VIDEO_MB}


def _save_upload(upload: UploadFile, folder: Path, max_mb: int) -> Path:
    ext = Path(upload.filename or "").suffix.lower()
    path = folder / f"{uuid.uuid4().hex}{ext}"
    size = 0
    with open(path, "wb") as f:
        while chunk := upload.file.read(1 << 20):
            size += len(chunk)
            if size > max_mb * 1024 * 1024:
                f.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, f"{upload.filename}: over {max_mb} MB")
            f.write(chunk)
    return path


def _alpha_map() -> np.ndarray | None:
    """Optional clean logo alpha map for true reverse alpha blending."""
    p = Path(__file__).with_name("logo_alpha.png")
    if p.exists():
        return cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    return None


def _file_response(path: Path, folder: Path, background: BackgroundTasks):
    background.add_task(shutil.rmtree, folder, ignore_errors=True)
    return FileResponse(path, filename=path.name.replace(
        path.name.split("_")[0] + "_", "clean_"))


@app.post("/api/remove/image")
async def remove_image(background: BackgroundTasks,
                       file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(415, f"Unsupported image type: {ext}")
    folder = Path(tempfile.mkdtemp())
    src = _save_upload(file, folder, MAX_IMAGE_MB)
    dst = folder / f"{src.stem}_clean{ext}"
    try:
        process_image_file(src, dst, _alpha_map())
    except ValueError as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(e))
    return _file_response(dst, folder, background)


@app.post("/api/remove/images")
async def remove_images(background: BackgroundTasks,
                        files: list[UploadFile] = File(...)):
    """Batch: up to MAX_IMAGES images, returns a ZIP of cleaned files."""
    if len(files) > MAX_IMAGES:
        raise HTTPException(413, f"At most {MAX_IMAGES} images per batch")
    folder = Path(tempfile.mkdtemp())
    out_dir = folder / "clean"
    out_dir.mkdir()
    alpha = _alpha_map()
    for i, file in enumerate(files):
        ext = Path(file.filename or "").suffix.lower()
        if ext not in IMAGE_EXTS:
            raise HTTPException(415, f"Unsupported image type: {ext}")
        src = _save_upload(file, folder, MAX_IMAGE_MB)
        dst = out_dir / f"clean_{i + 1}_{Path(file.filename).stem}{ext}"
        try:
            process_image_file(src, dst, alpha)
        except ValueError as e:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(422, str(e))
    zip_base = folder / "cleaned_images"
    shutil.make_archive(str(zip_base), "zip", out_dir)
    return _file_response(zip_base.with_suffix(".zip"), folder, background)


@app.post("/api/remove/video")
async def remove_video(background: BackgroundTasks,
                       file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(415, f"Unsupported video type: {ext}")
    folder = Path(tempfile.mkdtemp())
    src = _save_upload(file, folder, MAX_VIDEO_MB)
    dst = folder / f"{src.stem}_clean{ext}"
    try:
        meta = process_video_file(src, dst)
    except Exception as e:  # ffmpeg / decode errors
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, f"Video processing failed: {e}")
    resp = _file_response(dst, folder, background)
    resp.headers["X-Video-Frames"] = str(meta.get("frames", ""))
    resp.headers["X-Video-FPS"] = str(meta.get("fps", ""))
    return resp


@app.exception_handler(HTTPException)
async def http_errors(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


if FRONTEND.exists():  # serve the UI on the same origin
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="static")
