from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.users import router as users_router
from app.core.database import get_db
from app.api.auth import router as auth_router
from app.api.schools import router as schools_router

app = FastAPI(
    title="School ID Card API",
    version="1.0.0",
)

app.include_router(auth_router)
app.include_router(schools_router)
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