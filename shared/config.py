"""配置集中在应用工厂读取，测试不依赖全局环境或真实数据库。"""
from dataclasses import dataclass
import math
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    database_url: str = 'sqlite:///m3/data/m3.sqlite3'
    m1_base_url: str = 'http://127.0.0.1:8000'
    m1_mode: str = 'demo'
    m1_service_token: str = ''
    m2_base_url: str = ''
    sla_hours: float = 24
    poll_seconds: float = 2
    session_hours: float = 8
    service_token_m1: str = ''
    service_token_m2: str = ''

    @classmethod
    def from_env(cls):
        settings = cls(
            database_url=os.getenv('M3_DATABASE_URL', cls.database_url),
            m1_base_url=os.getenv('M3_M1_BASE_URL', cls.m1_base_url),
            m1_mode=os.getenv('M3_M1_MODE', 'demo'),
            m1_service_token=os.getenv('M3_M1_SERVICE_TOKEN', ''),
            m2_base_url=os.getenv('M3_M2_BASE_URL', ''),
            sla_hours=float(os.getenv('M3_SLA_HOURS', '24')),
            poll_seconds=float(os.getenv('M3_POLL_SECONDS', '2')),
            session_hours=float(os.getenv('M3_SESSION_HOURS', '8')),
            service_token_m1=os.getenv('M3_SERVICE_TOKEN_M1', ''),
            service_token_m2=os.getenv('M3_SERVICE_TOKEN_M2', ''),
        )
        settings.validate()
        return settings

    def validate(self):
        if any(not math.isfinite(x) or x <= 0 for x in (self.sla_hours, self.poll_seconds, self.session_hours)):
            raise ValueError('SLA、轮询周期、会话有效期必须大于零')
        for url in (self.m1_base_url, self.m2_base_url):
            if url and (urlsplit(url).scheme not in ('http', 'https') or not urlsplit(url).hostname):
                raise ValueError('模块地址必须为 HTTP(S) URL')
            if url and (urlsplit(url).username or urlsplit(url).password):
                raise ValueError('模块 URL 不能包含凭据，请使用独立服务密钥配置')
        if self.m1_mode not in ('demo', 'service'):
            raise ValueError('M3_M1_MODE 必须是 demo 或 service')
        if self.m1_mode == 'service' and not self.m1_service_token:
            raise ValueError('service 模式需要 M3_M1_SERVICE_TOKEN')
        tokens = [x for x in (self.service_token_m1, self.service_token_m2) if x]
        if any(len(x) < 32 for x in tokens) or len(tokens) != len(set(tokens)):
            raise ValueError('各模块服务凭据须独立且至少 32 字符')
