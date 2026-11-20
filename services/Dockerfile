FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /opt/goback

FROM base AS sandbox
COPY services/sandbox-manager/worker.py .
USER 65532:65532
WORKDIR /work
CMD ["sleep", "infinity"]

FROM base AS service
COPY services/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
COPY services /opt/goback/services

FROM service AS manager
RUN apt-get update \
    && apt-get install --yes --no-install-recommends sshfs openssh-client mount \
    && rm -rf /var/lib/apt/lists/*
