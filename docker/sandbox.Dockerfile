# The image GhostOS runs untrusted commands in (see ghostos/sandbox/docker.py).
# Build: docker build -f docker/sandbox.Dockerfile -t ghostos/sandbox:latest .
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pytest==8.3.4 \
 && useradd --uid 1000 --create-home sandbox

USER 1000:1000
WORKDIR /workspace
