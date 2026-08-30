import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.users import router as users_router
from app.core.database import get_db
from app.core.config import settings
from app.api.auth import router as auth_router
from app.api.schools import router as schools_router
from app.api.academic_sessions import router as academic_sessions_router
from app.api.classes import router as classes_router
from app.api.sections import router as sections_router
from app.api.students import router as students_router
from app.api.card_templates import router as card_templates_router
from app.api.student_fields import router as student_fields_router
logger = logging.getLogger(__name__)


app = FastAPI(
    title="School ID Card API",
    version="1.0.0",
)

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "uploads"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.mount(
    "/media",
    StaticFiles(directory=UPLOAD_DIR),
    name="media",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|https://idcard-flutter(?:-[a-z0-9-]+)?\.vercel\.app)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(schools_router)
app.include_router(academic_sessions_router)
app.include_router(classes_router)
app.include_router(sections_router)
app.include_router(students_router)
app.include_router(card_templates_router)
app.include_router(student_fields_router)
# ==========================================================
# Health Check
# ==========================================================

@app.get("/health")
def root():
    return {
        "status": "ok",
        "service": "ID Card Manager API",
        "docs": "/docs",
        "health": "/health",
    }

@app.get(
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

app.include_router(users_router)
