# Deploy anywhere Docker runs -- Hugging Face Spaces (free), Render, Fly.io...
# Hugging Face Spaces: create a Space with SDK = Docker, upload this whole
# project, and add this to the TOP of the Space's README.md:
#   ---
#   title: Gemini Watermark Remover
#   sdk: docker
#   app_port: 7860
#   ---
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY frontend/ frontend/

WORKDIR /app/backend
EXPOSE 7860
# Render/most hosts inject $PORT; Hugging Face Docker Spaces expect 7860.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
