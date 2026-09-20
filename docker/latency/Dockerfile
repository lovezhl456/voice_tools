# Build separately for each target architecture. VT_IMAGE explicitly selects this image.
ARG EXECUTOR_IMAGE=voice-tools-executor:0.13.1
FROM ${EXECUTOR_IMAGE}
USER root
WORKDIR /build/voice-tools
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src/ ./src/
# The base keeps existing tools; install this feature's current package, not its older CLI.
RUN python -m pip install --no-deps . \
    && python -m voice_tools.tools.latency.install --python /usr/local/bin/python --prefix /opt/latency \
    && chmod -R a+rX /opt/latency \
    && voice-tools --json latency doctor --latency-dir /opt/latency \
    && python -m pip check
ENV VOICE_TOOLS_LATENCY_DIR=/opt/latency
WORKDIR /work
USER 10001:10001
ENTRYPOINT ["voice-tools"]
CMD ["task", "--help"]
