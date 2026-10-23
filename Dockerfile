FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.title="Bosch Eyes Recorder" \
      org.opencontainers.image.description="Open-source recorder and web UI (live view + rolling recordings) for Bosch Eyes Gen1 cameras" \
      org.opencontainers.image.source="https://github.com/Alfagun74/bosch-eyes-recorder" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="1.2.0"

ARG TARGETARCH=amd64
ARG MEDIAMTX_VERSION=1.21.1

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    HEALTH_PORT=80 \
    TZ=Europe/Berlin \
    MEDIAMTX_BINARY=/usr/local/bin/mediamtx

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && case "${TARGETARCH}" in \
      amd64) MTX_ARCH="amd64" ;; \
      arm64) MTX_ARCH="arm64" ;; \
      *) echo "Nicht unterstuetzte Architektur: ${TARGETARCH} (amd64/arm64)" >&2; exit 1 ;; \
    esac \
 && curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/v${MEDIAMTX_VERSION}/mediamtx_v${MEDIAMTX_VERSION}_linux_${MTX_ARCH}.tar.gz" -o /tmp/mediamtx.tar.gz \
 && tar -xzf /tmp/mediamtx.tar.gz -C /usr/local/bin mediamtx \
 && chmod +x /usr/local/bin/mediamtx \
 && rm -f /tmp/mediamtx.tar.gz \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && test -x /usr/local/bin/mediamtx

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
