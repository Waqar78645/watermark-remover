# Gemini Watermark Remover — Full-Stack Clone

A working clone of the functionality of **geminiwatermark.io**: remove the
**visible** Gemini / Veo sparkle logo from images and videos.

## What the original does (analysis, 22 Oct 2026)

| Aspect | Original (geminiwatermark.io) | This clone |
|---|---|---|
| Core feature | Removes the visible Gemini & Veo sparkle from images **and** every video frame | Same |
| Headline limits | Up to 10 images · 20 MB each · single videos up to 50 MB | Same (env-configurable) |
| Image method | **Reverse alpha blending** — mathematically inverts the blend Google used to stamp the logo; pixels outside the logo stay byte-identical | Inpainting by default; true reverse alpha blending **is used instead when you drop a clean logo alpha map at `backend/logo_alpha.png`** (see `processing.reverse_alpha_blend`) |
| Video method | Logo tracked & removed frame-to-frame, original resolution / frame rate / bitrate, audio kept | One temporally-stable mask reused for all frames (no flicker), ffmpeg re-encode at the source bitrate & fps, original audio stream-copied |
| Where files run | **Locally in the browser (WebAssembly)** — nothing is uploaded | On **your server** (the architecture requested for this clone). Files live in a temp folder and are deleted when the download response finishes |
| Signup / price | Free, no signup | Free, no signup |
| Invisible watermark | SynthID is **not** removed (it is baked into the pixels) | Also **not** removed — no pixel editor can honestly promise that |

> Use this on content you generated / own. Removing a visible logo does not
> change a file's provenance, and the cleaned output should not be used to
> misrepresent AI-generated work as something it is not.

## Project structure

```
gemini-watermark-clone/
├── backend/
│   ├── app.py            # FastAPI server: upload validation, API, static UI
│   ├── processing.py     # detection + inpainting + video pipeline (OpenCV/ffmpeg)
│   ├── requirements.txt
│   └── logo_alpha.png    # OPTIONAL: clean logo alpha map -> reverse blending
├── frontend/
│   ├── index.html        # drag & drop UI, tabs (Images / Video)
│   ├── styles.css        # clean, modern, responsive styling
│   └── app.js            # validation, previews, Before/After slider, downloads
├── start.sh              # one-command run
└── README.md
```

## Run it

Prerequisites: Python 3.10+, and **ffmpeg** on PATH
(`apt install ffmpeg` / `brew install ffmpeg` / `choco install ffmpeg`).

```bash
./start.sh
# or manually:
cd backend && pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open <http://localhost:8000>.

## API

| Endpoint | Body | Returns |
|---|---|---|
| `GET /api/health` | — | limits & status |
| `POST /api/remove/image` | multipart `file` (image ≤ 20 MB) | cleaned image |
| `POST /api/remove/images` | multipart `files[]` (≤ 10 images) | ZIP of cleaned images |
| `POST /api/remove/video` | multipart `file` (video ≤ 50 MB) | cleaned video (+ `X-Video-Frames`, `X-Video-FPS` headers) |

## How the pipeline works

**Images** (`processing.remove_watermark_image`)
1. *Geometry prior* — Gemini stamps a square logo bottom-right: 48×48 / 32 px
   margin, or 96×96 / 64 px margin when the larger side exceeds 1024 px.
2. *Detection* — within the bottom-right search region, segment bright,
   low-saturation pixels (the white sparkle), keep the blob near the prior
   box; if detection fails, fall back to the prior box. Dilate for the halo.
   Tiled logos: run detection per tile region, or widen the search region —
   the mask builder is the single place to extend.
3. *Removal* — `cv2.inpaint` (Telea) + a feathered edge composite, so only
   masked pixels change. With `logo_alpha.png` present, exact reverse alpha
   blending is used for those pixels instead.

**Video** (`processing.process_video_file`)
1. `ffprobe` reads width, height, fps, bitrate, audio presence.
2. A single mask is computed from the temporal median of the first frames
   (logo is static, scene moves) and reused for **every** frame — this is
   what stops flicker.
3. Frames are inpainted with OpenCV and piped to ffmpeg at the original
   fps and bitrate target; the original audio track is then muxed back
   with `-c copy`, untouched.
4. Resolution is never changed; output keeps the source container
   (MP4 / MOV / WebM; MKV is accepted and returned as MKV with x264 inside).

## Known limitations

- Heavily compressed / screenshotted files: the sparkle blends into the
  background, detection weakens and inpainting leaves a soft patch.
- The corner detector assumes the standard Gemini/Veo corner placement;
  unusual crops need the search-region constants adjusted in
  `processing.py`.
- Video is necessarily re-encoded (frames change), so "same bitrate" is a
  target, not a bit-identical stream — exactly as with the original tool.
