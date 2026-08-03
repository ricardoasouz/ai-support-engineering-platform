"""Process setup plus conservative FastAPI and SQLAlchemy instrumentation."""

import logging
import time
from threading import Lock
from typing import Any
from weakref import WeakSet

from fastapi import FastAPI
from opentelemetry.trace import SpanKind
from sqlalchemy import Engine, event

from app.core.config import Settings
from app.observability.metrics import (
    configure_metrics,
    get_meter_provider,
    get_metrics,
    shutdown_metrics,
)
from app.observability.tracing import (
    configure_tracing,
    get_tracer_provider,
    mark_span_error,
    shutdown_tracing,
    start_span,
)

logger = logging.getLogger(__name__)
_configuration_lock = Lock()
_configured_service: str | None = None
_instrumented_engines: WeakSet[Engine] = WeakSet()


def configure_observability(
    settings: Settings,
    *,
    span_exporter: Any | None = None,
    metric_reader: Any | None = None,
    force: bool = False,
) -> None:
    """Initialize traces and metrics once without making telemetry a dependency."""
    global _configured_service
    with _configuration_lock:
        if _configured_service == settings.otel_service_name and not force:
            return
        try:
            configure_tracing(
                settings,
                span_exporter=span_exporter,
                force=force,
            )
            configure_metrics(
                settings,
                metric_reader=metric_reader,
                force=force,
            )
            _configured_service = settings.otel_service_name
        except Exception:
            # Configuration and exporter failures are never allowed to gate startup.
            logger.exception("telemetry_initialization_failed")


def shutdown_observability() -> None:
    """Best-effort flush for process shutdown."""
    global _configured_service
    shutdown_metrics()
    shutdown_tracing()
    _configured_service = None


def instrument_fastapi(app: FastAPI) -> None:
    """Add HTTP server spans without capturing headers or request bodies."""
    if getattr(app.state, "otel_instrumented", False):
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=get_tracer_provider(),
            meter_provider=get_meter_provider(),
            exclude_spans=["receive", "send"],
        )
        app.state.otel_instrumented = True
    except Exception:
        logger.exception("telemetry_fastapi_instrumentation_failed")


def _database_operation(statement: str) -> str:
    """Extract only a bounded operation verb; SQL text is deliberately discarded."""
    stripped = statement.lstrip(" \t\r\n(")
    token = stripped.split(None, 1)[0].upper() if stripped else "OTHER"
    return (
        token
        if token in {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}
        else "OTHER"
    )


def instrument_sqlalchemy_engine(engine: Engine) -> None:
    """Trace query duration and operation type without exporting SQL statements."""
    if engine in _instrumented_engines:
        return
    _instrumented_engines.add(engine)
    db_system = engine.dialect.name

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        execution_context: Any,
        _executemany: bool,
    ) -> None:
        operation = _database_operation(statement)
        manager = start_span(
            f"db {operation.lower()}",
            kind=SpanKind.CLIENT,
            attributes={
                "db.system.name": db_system,
                "db.operation.name": operation,
            },
        )
        span = manager.__enter__()
        execution_context._support_otel_state = (
            manager,
            span,
            operation,
            time.perf_counter(),
        )

    def finish(execution_context: Any, error: BaseException | None = None) -> None:
        state = getattr(execution_context, "_support_otel_state", None)
        if state is None:
            return
        execution_context._support_otel_state = None
        manager, span, operation, started = state
        outcome = "failure" if error is not None else "success"
        attributes = {
            "db_system": db_system,
            "operation": operation,
            "outcome": outcome,
        }
        get_metrics().count("database_operations", attributes=attributes)
        get_metrics().observe(
            "database_operation_duration_seconds",
            time.perf_counter() - started,
            attributes,
        )
        if error is not None:
            get_metrics().count(
                "database_failures",
                attributes={"db_system": db_system, "operation": operation},
            )
            mark_span_error(span, error)
        manager.__exit__(None, None, None)

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(
        _connection: Any,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        execution_context: Any,
        _executemany: bool,
    ) -> None:
        finish(execution_context)

    @event.listens_for(engine, "handle_error")
    def handle_error(exception_context: Any) -> None:
        execution_context = exception_context.execution_context
        if execution_context is not None:
            finish(execution_context, exception_context.original_exception)
