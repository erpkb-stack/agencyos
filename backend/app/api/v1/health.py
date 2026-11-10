import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config.settings import APP_VERSION, Settings, get_settings
from app.database.session import get_db
from app.schemas.common import HealthOut, ReadinessOut

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger("agencyos.health")


@router.get("", response_model=HealthOut)
def liveness(settings: Settings = Depends(get_settings)) -> HealthOut:
    return HealthOut(status="ok", version=APP_VERSION, environment=settings.app_env.value)


@router.get("/ready", response_model=ReadinessOut, responses={503: {"model": ReadinessOut}})
def readiness(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse({"status": "unavailable", "database": "error"}, status_code=503)
    return ReadinessOut(status="ok", database="ok")
