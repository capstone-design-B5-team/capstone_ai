import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from ai_backend.api.routes import health, verify
from ai_backend.config import get_settings
from ai_backend.db import check_connection, dispose_engine
from ai_backend.logging_config import configure_logging

settings = get_settings()
configure_logging(settings.log_level)

if settings.langchain_tracing_v2 and settings.langchain_api_key:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await check_connection()
    yield
    await dispose_engine()


app = FastAPI(
    lifespan=lifespan,
    title="AI Backend — 자료 검증 워크플로우",
    description="LangGraph 기반 자료 검증 (사실/출처/최신성/수치)",
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(verify.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "ai-backend",
        "version": "0.1.0",
        "env": settings.app_env,
    }
