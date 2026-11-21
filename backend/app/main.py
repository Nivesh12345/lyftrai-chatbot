import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db, llm
from .config import get_settings
from .schemas import ChatRequest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chatbot")


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init_db(settings.database_url)  # schema is created automatically
        llm.setup_cache(settings)
        logger.info(
            "started: %d key(s), %d model(s)",
            len(settings.llm_api_keys),
            len(settings.llm_models),
        )
        yield

    app = FastAPI(title="LLM Chatbot", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------- error handling: always JSON with a "detail" field ----------
    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        detail = [
            {"loc": list(e.get("loc", [])), "msg": str(e.get("msg", "")), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    # ---------- endpoints ----------
    @app.get("/health")
    def health():
        try:
            db.ping(settings.database_url)
        except Exception:
            raise HTTPException(status_code=503, detail="database unavailable")
        if len(settings.llm_api_keys) < 2:
            raise HTTPException(status_code=503, detail="at least 2 api keys required")
        return {
            "status": "ok",
            "keys_configured": len(settings.llm_api_keys),
            "cache_enabled": llm.cache_enabled(),
        }

    @app.post("/chat")
    def chat(req: ChatRequest, x_simulate_failure: str | None = Header(default=None)):
        started = time.perf_counter()

        with db.connect(settings.database_url) as conn:
            stored_prompt = db.get_session_prompt(conn, req.session_id)
            history = db.get_history(conn, req.session_id)

        requested_prompt = (req.system_prompt or "").strip() or None
        system_prompt = requested_prompt or stored_prompt or settings.default_system_prompt
        messages = (
            [{"role": "system", "content": system_prompt}]
            + [{"role": m["role"], "content": m["content"]} for m in history]
            + [{"role": "user", "content": req.message}]
        )

        simulate = (
            llm.parse_simulation(x_simulate_failure) if settings.enable_failure_simulation else {}
        )
        try:
            result = llm.complete(messages, settings, simulate)
        except llm.AllProvidersUnavailable:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        metadata = {
            "resolution_source": "LLM_CACHE" if result.cache_hit else "LIVE_INFERENCE",
            "model": result.model,
            "latency_ms": max(1, int((time.perf_counter() - started) * 1000)),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "key_id": result.key_id,
            "fallback_attempts": result.fallback_attempts,
        }
        # Only successful requests reach this point, so failures never enter history.
        db.save_exchange(
            settings.database_url, req.session_id, requested_prompt, req.message, result.reply, metadata
        )
        return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    def session_messages(session_id: str):
        with db.connect(settings.database_url) as conn:
            history = db.get_history(conn, session_id)
        if not history:
            raise HTTPException(status_code=404, detail="session not found")
        return {"session_id": session_id, "messages": history}

    return app


app = create_app()
