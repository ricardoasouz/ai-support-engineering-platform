"""Health-check response models."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response returned by the service health endpoint."""

    status: Literal["healthy"]
    service: Literal["ai-support-engineering-platform"]


class BuildInfoResponse(BaseModel):
    """Safe application and image build identifiers."""

    version: str
    git_sha: str
    build_time: str
