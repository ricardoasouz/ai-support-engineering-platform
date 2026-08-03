FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.lock ./
RUN python -m pip install --require-hashes --requirement requirements.lock

FROM gcr.io/distroless/cc-debian13@sha256:d97bc0a941b8d4be647dc0ee75b264ddbb772f1ac5ba690a4309c00723b23775 AS runtime

ARG APP_VERSION=development
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown

LABEL org.opencontainers.image.title="AI Support Engineering Platform" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIME}" \
      org.opencontainers.image.source="https://github.com/ricardoasouz/ai-support-engineering-platform"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    PATH="/usr/local/bin:${PATH}"

WORKDIR /app

COPY --from=builder /usr/local /usr/local
COPY --from=builder /usr/lib/x86_64-linux-gnu/libffi.so.8* /usr/lib/x86_64-linux-gnu/
COPY --chown=10001:10001 VERSION ./
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 migrations ./migrations
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 knowledge_base ./knowledge_base

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["/usr/local/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

ENTRYPOINT ["/usr/local/bin/python"]
CMD ["-m", "app.runtime", "api"]
