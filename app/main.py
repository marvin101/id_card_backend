import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.users import router as users_router
from app.core.database import get_db
from app.core.config import settings
from app.version import __version__
from app.api.auth import router as auth_router
from app.api.schools import router as schools_router
from app.api.academic_sessions import router as academic_sessions_router
from app.api.classes import router as classes_router
from app.api.sections import router as sections_router
from app.api.students import router as students_router
from app.api.student_grid import router as student_grid_router
from app.api.card_templates import public_router as public_designs_router, router as card_templates_router
from app.api.student_fields import router as student_fields_router
from app.api.student_imports import router as student_imports_router
from app.api.bulk_student_photos import router as bulk_student_photos_router
from app.api.public_forms import management_router as public_form_management_router, public_router as public_forms_router
logger = logging.getLogger(__name__)


async def _internal_server_error_response(
    _request: Request,
    _exception: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


api = FastAPI(
    title="CampusID API",
    version=__version__,
    exception_handlers={500: _internal_server_error_response},
)

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

api.mount(
    "/media",
    StaticFiles(directory=UPLOAD_DIR),
    name="media",
)
api.include_router(auth_router)
api.include_router(schools_router)
api.include_router(academic_sessions_router)
api.include_router(classes_router)
api.include_router(sections_router)
api.include_router(student_grid_router)
api.include_router(students_router)
api.include_router(card_templates_router)
api.include_router(public_designs_router)
api.include_router(student_fields_router)
api.include_router(student_imports_router)
api.include_router(bulk_student_photos_router)
api.include_router(public_form_management_router)
api.include_router(public_forms_router)
# ==========================================================
# Health Check
# ==========================================================

@api.get("/health")
def root():
    return {
        "status": "ok",
        "service": "CampusID API",
        "docs": "/docs",
        "health": "/health",
    }

@api.get(
    "/health/check",
    responses={503: {"description": "Database is unavailable."}},
)
def health_check(
    response: Response,
    db: Session = Depends(get_db),
):
    try:
        result = db.execute(
            text(
                """
                SELECT
                    current_database(),
                    current_user,
                    inet_server_addr()
                """
            )
        ).mappings().one()

        return {
            "status": "ok",
            "api": "running",
            "database": "connected",
        }

    except Exception:
        logger.error("Database readiness check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "error",
            "api": "running",
            "database": "disconnected",
        }


# ==========================================================
# API Routers
# ==========================================================

api.include_router(users_router)

# Keep CORS outside FastAPI's error middleware so even unexpected 500 responses
# receive the browser-facing CORS headers. Expose the two FastAPI hooks used by
# tests and API tooling while retaining the wrapped ASGI app as the Uvicorn entry.
app = CORSMiddleware(
    api,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.dependency_overrides = api.dependency_overrides
app.openapi = api.openapi
