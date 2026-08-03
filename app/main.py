"""FastAPI application entry point."""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi import Request as FastAPIRequest

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.events.dependencies import get_outbox_retry_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Configure process-wide resources for the application lifetime."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("application_started")
    retry_service = None
    if settings.kafka_enabled:
        retry_service = get_outbox_retry_service()
        retry_service.start()
    try:
        yield
    finally:
        if retry_service is not None:
            retry_service.stop()
        logger.info("application_stopped")


app = FastAPI(
    title="AI Support Engineering Platform",
    description="Event-driven deterministic incident analysis API for support teams.",
    version="0.3.0",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.middleware("http")
async def log_http_request(request: FastAPIRequest, call_next):  # type: ignore[no-untyped-def]
    """Emit one structured completion log for every HTTP request."""
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "http_request_failed",
            extra={"method": request.method, "path": request.url.path},
        )
        raise

    logger.info(
        "http_request_completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((time.perf_counter() - started_at) * 1_000, 2),
        },
    )
    return response
