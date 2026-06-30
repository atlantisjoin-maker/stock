FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    ASTOCK_HOME=/data \
    ASTOCK_HOST=0.0.0.0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md MANIFEST.in ./
COPY requirements-base.txt requirements-fund.txt requirements-ocr.txt ./
COPY src ./src
COPY examples ./examples

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e ".[ocr,fund]" \
    && useradd --create-home --shell /usr/sbin/nologin astock \
    && mkdir -p /data \
    && chown -R astock:astock /data /app

USER astock

EXPOSE 8765

CMD ["sh", "-c", "python -m astock_terminal --host ${ASTOCK_HOST:-0.0.0.0} --port ${PORT:-8765} --no-browser"]
