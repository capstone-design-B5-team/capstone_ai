"""Health check 엔드포인트.

배포 환경의 liveness/readiness probe용.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
