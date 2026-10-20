FROM python:3.11-slim

# uv: same installer as local dev, ~10x faster than pip
COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /usr/local/bin/uv
# opencv (pulled in by docling's OCR) needs these shared libs at import time
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Use the image's Python (never download one); the venv's bin goes first on PATH
ENV UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 PATH="/app/backend/.venv/bin:$PATH"
WORKDIR /app/backend

# Exact versions from uv.lock (CPU-only torch on Linux, see pyproject.toml); --locked fails the
# build if the lock is stale. Dependencies only, so this layer is rebuilt only when they change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY app ./app
EXPOSE 8000
# 0.0.0.0, not 127.0.0.1: inside a container localhost is unreachable from the host
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
