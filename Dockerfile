FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Bosch Eyes Recorder" \
      org.opencontainers.image.description="Open-source recorder and web UI (live view + rolling recordings) for Bosch Eyes Gen1 cameras" \
      org.opencontainers.image.source="https://github.com/Alfagun74/bosch-eyes-recorder" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="1.4.0"

ARG TARGETARCH
ARG MEDIAMTX_VERSION=1.21.1

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HEALTH_PORT=80 \
    TZ=Europe/Berlin \
    MEDIAMTX_BINARY=/usr/local/bin/mediamtx \
    FFMPEG_BINARY=/usr/lib/jellyfin-ffmpeg/ffmpeg \
    LIBVA_DRIVER_NAME=iHD

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    ARCH="${TARGETARCH:-$(uname -m)}"; \
    case "$ARCH" in \
      amd64|x86_64) MTX_ARCH="amd64" ;; \
      arm64|aarch64) MTX_ARCH="arm64" ;; \
      *) echo "Unsupported architecture: $ARCH (amd64/arm64)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_${MTX_ARCH}.tar.gz" -o /tmp/mediamtx.tar.gz; \
    tar -xzf /tmp/mediamtx.tar.gz -C /usr/local/bin mediamtx; \
    chmod +x /usr/local/bin/mediamtx; \
    rm -f /tmp/mediamtx.tar.gz; \
    test -x /usr/local/bin/mediamtx; \
    if [ "$ARCH" = "amd64" ] || [ "$ARCH" = "x86_64" ]; then \
      mkdir -p /etc/apt/keyrings; \
      curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key | gpg --batch --yes --dearmor -o /etc/apt/keyrings/jellyfin.gpg; \
      echo "deb [signed-by=/etc/apt/keyrings/jellyfin.gpg] https://repo.jellyfin.org/debian bookworm main" > /etc/apt/sources.list.d/jellyfin.list; \
      apt-get update; \
      apt-get install -y --no-install-recommends jellyfin-ffmpeg8; \
      ln -sf /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg; \
      ln -sf /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe; \
      if [ -e /usr/lib/jellyfin-ffmpeg/vainfo ]; then ln -sf /usr/lib/jellyfin-ffmpeg/vainfo /usr/local/bin/vainfo; fi; \
      /usr/lib/jellyfin-ffmpeg/ffmpeg -hide_banner -encoders > /tmp/encoders.txt; \
      grep -q 'av1_qsv' /tmp/encoders.txt; \
      grep -q 'libopus' /tmp/encoders.txt; \
      rm -f /tmp/encoders.txt; \
    fi; \
    apt-get purge -y curl gnupg; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 80

HEALTHCHECK --interval=60s --timeout=5s --start-period=45s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:80/healthz', timeout=4)"

CMD ["python", "-m", "app.main"]
