from fastapi import APIRouter

from verikyb import __version__
from verikyb.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", summary="Report that the API is up, and its version")
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
