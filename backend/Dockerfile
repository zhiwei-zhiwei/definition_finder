FROM python:3.11-slim

# uv: same installer as local dev, ~10x faster than pip
COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /usr/local/bin/uv
# opencv (pulled in by docling's OCR) needs these shared libs at import time
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV UV_SYSTEM_PYTHON=1 UV_NO_CACHE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend

# CPU-only torch first: PyPI's Linux torch wheel ships ~3 GB of CUDA libs this image never uses
RUN uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
# Dependencies only (not the app), so this layer is rebuilt only when pyproject.toml changes
COPY pyproject.toml .
RUN uv pip install -r pyproject.toml

COPY app ./app
EXPOSE 8000
# 0.0.0.0, not 127.0.0.1: inside a container localhost is unreachable from the host
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
