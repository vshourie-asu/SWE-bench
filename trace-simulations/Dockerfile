# trace-simulations/Dockerfile
FROM python:3.11-slim

# Docker CLI + git are needed so SWE-bench can drive the host Docker daemon and
# check out task repos. Use `docker-cli` (just the client binary), NOT `docker.io`:
# on Debian 13 (trixie, what python:3.11-slim now tracks) the `docker.io` package no
# longer ships the `docker` client at /usr/bin/docker, so the harness's
# subprocess.run(["docker", ...]) calls fail with "No such file or directory: 'docker'".
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates docker-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/SWE-bench

# Copy the repo in and install SWE-bench + mini-SWE-agent (the inference agent).
COPY . /opt/SWE-bench
RUN pip install --no-cache-dir -e . \
    && pip install --no-cache-dir mini-swe-agent

# mini-SWE-agent runs inference; the harness drives Docker on the host.
CMD ["bash"]