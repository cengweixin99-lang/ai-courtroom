from fastapi import APIRouter

from mootcourt.api.routes.agents import router as agents_router
from mootcourt.api.routes.cases import router as cases_router
from mootcourt.api.routes.health import router as health_router
from mootcourt.api.routes.sessions import router as sessions_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(cases_router)
api_router.include_router(sessions_router)
api_router.include_router(agents_router)
