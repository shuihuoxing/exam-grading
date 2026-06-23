"""FastAPI 入口。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .api.routes import router


def _cors_list() -> list[str]:
    raw = settings.cors_origins.strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="自动阅卷系统", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/")
def root():
    return {"name": "自动阅卷系统", "docs": "/docs"}
