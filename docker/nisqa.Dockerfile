ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/tmp/cache \
    NUMBA_CACHE_DIR=/tmp/numba

WORKDIR /opt/voice-tools
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src/ ./src/

# Use CPU wheels first so Linux x86_64 does not install CUDA packages.
# SoundFile's Linux wheels include libsndfile; the smoke test checks decoding.
RUN python -m pip install --upgrade pip \
    && python -m pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install '.[nisqa]' \
    && python -m pip check

# Use --user with the host UID/GID for writable bind mounts on Linux.
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["voice-tools"]
CMD ["nisqa", "--help"]
