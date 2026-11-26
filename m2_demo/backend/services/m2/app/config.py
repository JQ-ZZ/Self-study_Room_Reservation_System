from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """全部配置来自 M2_ 前缀环境变量，默认值面向本地演示。"""

    database_url: str = os.getenv("M2_DATABASE_URL", "sqlite:///./m2.db")
    m1_base_url: str = os.getenv("M2_M1_BASE_URL", "http://localhost:8081")
    m1_timeout_seconds: float = float(os.getenv("M2_M1_TIMEOUT_SECONDS", "3"))
    # A 的 demo 用 X-Demo-User 模拟管理员；正式版必须换成 C 的公共认证/服务 token。
    m1_demo_user: str = os.getenv("M2_M1_DEMO_USER", "admin")
    jwt_secret: str = os.getenv("M2_JWT_SECRET", "demo-only-secret-change-me")
    jwt_algorithm: str = "HS256"
    token_ttl_hours: int = int(os.getenv("M2_TOKEN_TTL_HOURS", "8"))

    # 审批超时：演示环境 90 秒，生产建议 86400（24 小时）
    approval_timeout_seconds: int = int(os.getenv("M2_APPROVAL_TIMEOUT_SECONDS", "90"))
    max_application_hours: int = int(os.getenv("M2_MAX_APPLICATION_HOURS", "12"))

    # 同步重试
    sync_max_attempts: int = int(os.getenv("M2_SYNC_MAX_ATTEMPTS", "5"))
    sync_interval_seconds: float = float(os.getenv("M2_SYNC_INTERVAL_SECONDS", "3"))
    worker_enabled: bool = os.getenv("M2_WORKER_ENABLED", "true").lower() == "true"

    auto_create_tables: bool = os.getenv("M2_AUTO_CREATE_TABLES", "false").lower() == "true"
    demo_mode: bool = os.getenv("M2_DEMO_MODE", "true").lower() == "true"
