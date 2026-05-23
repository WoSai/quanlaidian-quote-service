from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.api import quote, health, files, shortlink
from app.errors import register_exception_handlers
from app.persistence import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.config import settings  # late import: respects test monkeypatch
    init_db(settings.data_root / "quote.db")
    yield


app = FastAPI(title="Quanlaidian Quote Service", version="1.0.0", lifespan=lifespan)

register_exception_handlers(app)

# Middleware to set request_id for error responses
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    if not hasattr(request.state, "request_id"):
        request.state.request_id = "unknown"
    response = await call_next(request)
    return response

app.include_router(quote.router)
app.include_router(health.router)
app.include_router(files.router)
app.include_router(shortlink.router)
