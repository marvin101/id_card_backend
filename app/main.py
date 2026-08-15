from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.users import router as users_router
from app.core.database import get_db
from app.api.auth import router as auth_router
from app.api.schools import router as schools_router
from app.api.academic_sessions import router as academic_sessions_router
from app.api.classes import router as classes_router
from app.api.sections import router as sections_router
from app.api.students import router as students_router

app = FastAPI(
    title="School ID Card API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:51698",
        "http://127.0.0.1:51698",
    ],
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
# ==========================================================
# Health Check
# ==========================================================

@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))

        return {
            "status": "ok",
            "api": "running",
            "database": "connected",
        }

    except Exception:
        return {
            "status": "error",
            "api": "running",
            "database": "disconnected",
        }


# ==========================================================
# API Routers
# ==========================================================

app.include_router(users_router)