"""VoiceHub AI Gateway — 运行期设置服务。

优先级：gw_settings（DB）> 环境变量 > 内置默认。
管理台设置页写 DB；轮询/抽查/归档/清理等后台任务每周期经 get_settings()
重读快照，实现「保存即热生效」。DB 读取异常（如测试库未建表）静默降级。

约定：
- 键必须先登记在 DEFAULTS 中（防止任意键写入）；
- DB 值为空串视为「未设置」，回退下一级（页面上即“清除覆盖”）；
- 函数内局部导入 db 模块，保持与测试 fixture 的 monkeypatch 兼容。
"""
from __future__ import annotations

from typing import Any

from .config import settings as env_settings

# 运行期可调项（key 与 config.Settings 字段同名者以环境变量为第二级）
DEFAULTS: dict[str, str] = {
    # 轮询
    "review_scenes": "register,note",
    "poll_interval_seconds": "30",
    "poll_batch_size": "20",
    "review_cooldown_seconds": "300",
    # L2 质量
    "llm_confidence_threshold": "0.60",  # APPROVE 且置信度低于阈值 → REVIEW
    "l3_enabled": "true",                # 语种链路是否允许 L3 搜索
    "language_whitelist": "",            # 逗号分隔白名单；空=不限制
    # 抽查
    "spotcheck_enabled": "false",
    "spotcheck_interval_hours": "24",
    "spotcheck_batch_size": "20",
    # 归档 / 清理
    "archive_interval_hours": "24",
    "archive_keep": "7",
    "log_retention_days": "180",
    # 注册风控
    "register_channel_frozen": "false",  # true = 冻结注册通道（本轮跳过 register 场景）
}

# 分组展示（设置页顺序）；label 用中文短句
GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("轮询", [
        ("review_scenes", "审核场景（逗号分隔）"),
        ("poll_interval_seconds", "轮询间隔（秒）"),
        ("poll_batch_size", "每轮批量"),
        ("review_cooldown_seconds", "REVIEW 冷却（秒）"),
    ]),
    ("AI 质量与语种", [
        ("llm_confidence_threshold", "APPROVE 置信阈值（低于转人工）"),
        ("l3_enabled", "L3 搜索开关"),
        ("language_whitelist", "语种白名单（逗号分隔，空=不限）"),
    ]),
    ("定期抽查", [
        ("spotcheck_enabled", "抽查启用"),
        ("spotcheck_interval_hours", "抽查间隔（小时）"),
        ("spotcheck_batch_size", "每次抽样条数"),
    ]),
    ("归档与留存", [
        ("archive_interval_hours", "归档间隔（小时）"),
        ("archive_keep", "归档保留份数"),
        ("log_retention_days", "日志保留天数"),
    ]),
    ("注册风控", [
        ("register_channel_frozen", "冻结注册通道"),
    ]),
]

# 数值键：set_setting 强校验（防坏阈值静默关闭质量门等兜底）
NUMERIC_KEYS = {
    "poll_interval_seconds", "poll_batch_size", "review_cooldown_seconds",
    "llm_confidence_threshold", "spotcheck_interval_hours", "spotcheck_batch_size",
    "archive_interval_hours", "archive_keep", "log_retention_days",
}


def _db_values() -> dict[str, str]:
    from .db import GatewaySession, GwSetting
    session = GatewaySession()
    try:
        rows = session.query(GwSetting).all()
        return {r.key: (r.value or "") for r in rows}
    except Exception:
        # 测试环境未建表 / 库不可用：降级 env/default
        return {}
    finally:
        session.close()


def _resolve(key: str, db: dict[str, str]) -> str:
    raw = db.get(key, "")
    if raw.strip():
        return raw.strip()
    env_val: Any = getattr(env_settings, key, None)
    if env_val is not None and str(env_val).strip():
        return str(env_val)
    return DEFAULTS[key]


def get_setting(key: str) -> str:
    """单键读取（DB > env > 默认）。未登记键抛 KeyError。"""
    if key not in DEFAULTS:
        raise KeyError(key)
    return _resolve(key, _db_values())


def get_settings() -> dict[str, str]:
    """全量快照（每周期重读一次即可）。"""
    db = _db_values()
    return {k: _resolve(k, db) for k in DEFAULTS}


def set_setting(key: str, value: str) -> None:
    """写 DB；value 为空串则删除该行（恢复 env/默认）。调用方负责权限与审计。

    数值键（NUMERIC_KEYS）非法值抛 ValueError —— 坏配置必须在写入即被拦截，
    而不是在质量门/轮询处静默降级。
    """
    if key not in DEFAULTS:
        raise KeyError(key)
    from .db import GatewaySession, GwSetting
    v = (value or "").strip()
    if v and key in NUMERIC_KEYS:
        f = float(v)  # ValueError → 上层提示
        if key == "llm_confidence_threshold" and not 0 < f <= 1:
            raise ValueError("llm_confidence_threshold 取值应为 (0, 1]")
        if key != "llm_confidence_threshold" and f <= 0:
            raise ValueError(f"{key} 必须为正数")
    session = GatewaySession()
    try:
        row = session.query(GwSetting).filter_by(key=key).one_or_none()
        if v == "":
            if row is not None:
                session.delete(row)
                session.commit()
            return
        if row is None:
            session.add(GwSetting(key=key, value=v))
        else:
            row.value = v
        session.commit()
    finally:
        session.close()


def setting_sources() -> dict[str, str]:
    """每个键当前生效值的来源：db / env / default（设置页展示）。"""
    db = _db_values()
    sources: dict[str, str] = {}
    for k in DEFAULTS:
        if db.get(k, "").strip():
            sources[k] = "db"
        elif getattr(env_settings, k, None) not in (None, ""):
            sources[k] = "env"
        else:
            sources[k] = "default"
    return sources


def parse_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")
