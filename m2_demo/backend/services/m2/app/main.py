from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .db import make_engine, make_session_factory
from .m1_client import M1Client
from .models import Base
from .routers import applications, auth, meta, ops, rooms
from .worker import Sweeper

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("m2")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: Settings | None = None, m1_client: M1Client | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        if settings.auto_create_tables:
            Base.metadata.create_all(engine)
        session_factory = make_session_factory(engine)
        client = m1_client or M1Client(
            settings.m1_base_url,
            settings.m1_timeout_seconds,
            settings.m1_demo_user,
        )

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.m1_client = client
        app.state.sweep_lock = threading.Lock()

        sweeper = None
        if settings.worker_enabled:
            sweeper = Sweeper(session_factory, settings, client, app.state.sweep_lock)
            sweeper.start()
            logger.info("sweeper started (interval=%ss)", settings.sync_interval_seconds)

        logger.info("M2 started, m1=%s, db=%s", settings.m1_base_url, settings.database_url)
        try:
            yield
        finally:
            if sweeper is not None:
                sweeper.stop()
            client.close()
            engine.dispose()
            logger.info("M2 stopped")

    app = FastAPI(
        title="M2 整教室预约与审批",
        version="0.1.0-demo",
        description="软件过程管理 PJ · M2 演示服务（两级审批 + 超时撤销 + M2→M1 状态写回）",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (auth, rooms, applications, ops, meta):
        app.include_router(module.router, prefix="/api/v1")
    # meta.router 里已经有 /health、/version，额外暴露一份根路径，方便健康检查
    app.include_router(meta.router)

    if WEB_DIR.exists():
        app.mount("/demo", StaticFiles(directory=str(WEB_DIR), html=True), name="demo")

    return app


app = create_app()
