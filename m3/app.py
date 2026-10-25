import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi import Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm.exc import StaleDataError

from m3.m1_client import M1Client
from m3.routes import router as tickets_router
from m3.worker import tick
from shared.config import Settings
from shared.db import database, now
from shared.routes import router as common_router
from shared.security import current_user

ROOT = Path(__file__).parent
logger = logging.getLogger(__name__)


def create_app(settings=None, m1=None, clock=now, background=True):
    settings = settings or Settings.from_env()
    settings.validate()
    engine, sessions = database(settings.database_url)

    async def loop(app, stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(tick, app)
            except Exception:
                logger.exception('M3 后台任务失败；租约到期后将恢复')
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.poll_seconds)
            except asyncio.TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(app):
        # 迁移必须显式执行，启动应用不偷偷改 schema。
        with engine.connect() as connection:
            revision = connection.execute(text('SELECT version_num FROM c_alembic_version')).scalar()
            if revision != 'c0001':
                raise RuntimeError('请先执行 python -m m3.manage migrate')
        stop = asyncio.Event()
        task = asyncio.create_task(loop(app, stop)) if background else None
        try:
            yield
        finally:
            stop.set()
            if task:
                await task
            app.state.m1.close()
            engine.dispose()

    app = FastAPI(title='栖学 · M3 物业工单与公共服务', version='1.0.0', lifespan=lifespan)
    app.state.settings, app.state.engine, app.state.sessions = settings, engine, sessions
    app.state.clock, app.state.m1 = clock, m1 or M1Client(settings)
    app.include_router(common_router)
    app.include_router(tickets_router)
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

    @app.exception_handler(StaleDataError)
    async def concurrent_update(request, exc):
        return JSONResponse(status_code=409, content={'detail': '工单已被其他请求更新，请刷新后重试'})

    @app.middleware('http')
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/', include_in_schema=False)
    def page():
        return FileResponse(ROOT / 'static/index.html')

    @app.get('/health')
    def health():
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        return {'status': 'ok', 'module': 'M3'}

    @app.get('/api/platform')
    def platform(user=Depends(current_user)):
        return {'modules': [
            {'name': 'M1 · 自习座位预约', 'url': settings.m1_base_url,
             'note': '独立 M1 演示入口，尚未接入统一认证'},
            {'name': 'M2 · 教室预约审批', 'url': settings.m2_base_url,
             'note': '由成员 B 配置入口并接入统一认证'},
        ]}

    return app
