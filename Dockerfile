FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Singapore

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    rsgain \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY tests ./tests

RUN pip install --no-cache-dir -e . \
 && pip install --no-cache-dir 'pytest>=8.0,<9.2' pytest-asyncio \
 && python -m venv /opt/musicdl \
 && /opt/musicdl/bin/pip install --no-cache-dir musicdl==2.14.2 \
 && playwright install --with-deps chromium

VOLUME ["/app/data", "/music"]
EXPOSE 9000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
