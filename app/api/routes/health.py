"""Service health endpoint."""

from fastapi import APIRouter, status

from app.models.health import BuildInfoResponse, HealthResponse
from app.version import get_build_metadata

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Check service health",
)
def health_check() -> HealthResponse:
    """Report whether the API process is available."""
    return HealthResponse(
        status="healthy",
        service="ai-support-engineering-platform",
    )


@router.get(
    "/build",
    response_model=BuildInfoResponse,
    status_code=status.HTTP_200_OK,
    summary="Get safe application build metadata",
)
def build_info() -> BuildInfoResponse:
    """Expose version, Git SHA, and build time with safe local fallbacks."""
    return BuildInfoResponse(**vars(get_build_metadata()))
