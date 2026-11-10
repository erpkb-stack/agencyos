from fastapi import APIRouter, Depends

from app.api.v1 import agents, approvals, auth, ceo, dashboard, health, system_settings, tasks
from app.security.deps import enforce_api_rate_limit, enforce_csrf

api_router = APIRouter(
    prefix="/api/v1", dependencies=[Depends(enforce_api_rate_limit), Depends(enforce_csrf)]
)
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(system_settings.router)
api_router.include_router(ceo.router)
api_router.include_router(agents.router)
api_router.include_router(tasks.router)
api_router.include_router(approvals.router)
