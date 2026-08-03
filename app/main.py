"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.router import api_router

app = FastAPI(
    title="AI Support Engineering Platform",
    description="Deterministic incident analysis API for technical support teams.",
    version="0.1.0",
)
app.include_router(api_router)
