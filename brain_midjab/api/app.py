"""
MidJab V3 — FastAPI Application
================================

Main API entrypoint. Run with:
    cd brain_midjab && uvicorn api.app:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.rate_limit import limiter
from api.routes import resume_router, pipeline_router
from config.database import test_connection
from core import orm_models  # noqa: F401 — ensure models are registered


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup / shutdown hook — verifies DB is reachable."""
    if test_connection():
        print(" PostgreSQL connection OK")
    else:
        print("  PostgreSQL unreachable — some endpoints will fail")
    yield
    print(" Shutting down MidJab API")


app = FastAPI(
    title="MidJab V3 API",
    description="Resume parsing, job matching & application pipeline",
    version="3.0.0",
    lifespan=lifespan,
)

# ── Rate limiting ──────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS — allow the Next.js frontend (Better Auth) to call this API ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # Next.js dev
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,        # needed to forward session cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ─────────────────────────────────────────────────────────────
app.include_router(resume_router, prefix="/api/resume", tags=["resume"])
app.include_router(pipeline_router, prefix="/api/pipeline", tags=["pipeline"])


@app.get("/api/health")
def health():
    return {"status": "ok", "db": test_connection()}
