# ---- Stage 1: build the support-dashboard frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Output assumed at /frontend/dist — adjust if your vite.config.ts outDir differs

# ---- Stage 2: backend runtime ----
FROM python:3.10-slim
WORKDIR /app

# System deps for OpenCV, which is still used by the profile-photo upload
# route. insightface/onnxruntime (and the build-essential + model-bake step
# they required) were removed — Railway no longer runs any face-recognition
# code; that now only runs on the Local Node.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bring in the built frontend so Flask's SPA fallback (frontend/dist) can serve it
COPY --from=frontend-build /frontend/dist ./frontend/dist

RUN mkdir -p logs uploads models

# Railway injects $PORT at runtime — do not hardcode 5000
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",8080)}/api/health')"

# gunicorn, not the Flask dev server. --workers 1 was originally load-bearing
# because the face-recognition model was cached per-process (loading it in
# multiple workers would multiply RAM use); that constraint is gone now that
# recognition no longer runs on Railway. Worker count left unchanged here —
# revisit if you want more concurrency, since --threads alone still serializes
# CPU-bound work.
# JSON exec form wrapping sh -c: keeps proper SIGTERM handling on
# restarts/redeploys while still allowing ${PORT} shell expansion
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 --timeout 120"]