FROM ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl git unzip zip \
    python3-dev python3-venv python3-pip ffmpeg time \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/voice-tools
COPY pyproject.toml README.md ./
COPY src ./src
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && /opt/venv/bin/python -m pip install --no-cache-dir --no-build-isolation .
COPY scripts/install_visqol.py scripts/visqol_demo.py scripts/visqol_linux_smoke.py scripts/visqol_export_runtime.py ./scripts/
COPY tests/visqol ./tests/visqol

ENV PATH="/opt/venv/bin:${PATH}" \
    VOICE_TOOLS_VISQOL_DIR=/opt/visqol/source
WORKDIR /data
ENTRYPOINT ["voice-tools"]
CMD ["visqol", "--help"]
