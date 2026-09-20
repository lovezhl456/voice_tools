# Build on each target architecture: linux/amd64 or linux/arm64.
ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG NATIVE_IMAGE=native
FROM ${PYTHON_IMAGE} AS native
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git curl ca-certificates swig pkg-config libssl-dev libasound2-dev unzip zip && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir setuptools wheel
WORKDIR /build
RUN curl -fL https://github.com/pjsip/pjproject/archive/refs/tags/2.17.tar.gz -o pj.tar.gz \
    && sha256sum pj.tar.gz > pj-source.sha256 && tar xzf pj.tar.gz \
    && cd pjproject-2.17 && CFLAGS=-fPIC CXXFLAGS=-fPIC ./configure --disable-video --disable-sound \
    && make dep && make -j2 \
    && cd pjsip-apps/src/swig/python && make wheel PYTHON_EXE=/usr/local/bin/python \
    && mkdir /wheels && cp dist/*.whl /wheels/
COPY scripts/install_visqol.py scripts/visqol_export_runtime.py /build/
COPY pyproject.toml README.md /build/
COPY src /build/src
RUN python -m pip install /build
RUN python /build/install_visqol.py --prefix /build/visqol --jobs 2 --memory-mb 4096 \
    && python /build/visqol_export_runtime.py --visqol-dir /build/visqol/source --out /build/backend

FROM ${NATIVE_IMAGE} AS native_backend

FROM ${PYTHON_IMAGE}
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 XDG_CACHE_HOME=/tmp/cache NUMBA_CACHE_DIR=/tmp/numba VOICE_TOOLS_VISQOL_DIR=/opt/visqol/source
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tshark sip-tester openssh-client ca-certificates libgomp1 libstdc++6 libasound2 libssl3 libcap2-bin \
    && setcap cap_net_raw+ep /usr/bin/sipp && setcap cap_net_raw+ep /usr/bin/dumpcap && rm -rf /var/lib/apt/lists/*
COPY --from=native_backend /wheels /wheels
COPY --from=native_backend /build/pj-source.sha256 /opt/native-pj-source.sha256
COPY --from=native_backend /build/backend /opt/visqol
WORKDIR /opt/voice-tools
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN python -m pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install '.[nisqa,vad]' /wheels/*.whl && rm -rf /wheels \
    && python -m pip check && python -c 'import pjsua2' \
    && voice-tools --json visqol doctor
WORKDIR /work
USER 10001:10001
ENTRYPOINT ["voice-tools"]
CMD ["task", "--help"]
