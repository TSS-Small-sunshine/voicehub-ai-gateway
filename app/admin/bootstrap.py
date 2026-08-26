"""VoiceHub AI Gateway — 首启初始化管理员（环境变量驱动）。"""
from __future__ import annotations

import logging

from ..auth import create_user
from ..config import settings
from ..db import GatewaySession, GwUser, init_db

log = logging.getLogger("ai-gateway")


def ensure_admin() -> None:
    """若 gateway_users 表为空且环境变量给了 ADMIN_INIT_USER/PASS，创建首个管理员。"""
    init_db()
    session = GatewaySession()
    try:
        count = session.query(GwUser).count()
        if count > 0:
            return
        user = (settings.admin_init_user or "").strip()
        pwd = settings.admin_init_pass or ""
        if not user or not pwd:
            log.warning("首启尚未创建管理员：请设置环境变量 ADMIN_INIT_USER 与 ADMIN_INIT_PASS 后重启，或手动创建")
            return
        create_user(session, user, pwd, role="admin", must_change_password=True)
        log.info("首启管理员已创建：%s（首次登录后强制改密）", user)
    finally:
        session.close()