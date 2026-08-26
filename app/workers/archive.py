"""VoiceHub AI Gateway — 本地归档 worker（周期快照至 data/archive/，SPEC [S13]）。

仅支持 SQLite 连接串（校方默认形态）；非 SQLite 记日志跳过。
快照用 sqlite3 backup API（在线一致性拷贝），保留最近 N 份，全部留本地。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import settings
from ..settings import get_settings

log = logging.getLogger("ai-gateway")


def sqlite_path(url: str | None) -> Path | None:
    """sqlite:/// 前缀 → 文件路径；其余（postgres:// 等）返回 None。"""
    if not url or not url.startswith("sqlite:///"):
        return None
    raw = url[len("sqlite:///"):]
    return Path(raw) if raw else None


def archive_now(dest_dir: str | None = None, keep: int | None = None) -> list[str]:
    """立即归档一次，返回本次生成的文件路径列表。"""
    cfg = get_settings()
    dest = Path(dest_dir or "data/archive")
    n_keep = keep if keep is not None else int(float(cfg.get("archive_keep") or 7))
    dest.mkdir(parents=True, exist_ok=True)

    urls = [settings.database_url, settings.gateway_database_url or settings.database_url]
    srcs: list[Path] = []
    for u in urls:
        p = sqlite_path(u)
        if p is None:
            log.info("归档跳过非 SQLite 数据源")
            continue
        if p.exists() and p.resolve() not in {s.resolve() for s in srcs}:
            srcs.append(p)

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    created: list[str] = []
    for src in srcs:
        target = dest / f"{src.stem}_{stamp}.db"
        dst_conn = sqlite3.connect(str(target))
        try:
            src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                src_conn.backup(dst_conn)
            finally:
                src_conn.close()
        finally:
            dst_conn.close()
        created.append(str(target))
        log.info("归档完成：%s → %s", src, target)

    # 按库前缀分组各保留 N 份（仅识别本任务产出的 {stem}_{时间戳}.db，防误删外部放入的同名库）
    import re as _re
    _ours = _re.compile(r"^[\w-]+_\d{8}T\d{6}Z\.db$")
    by_stem: dict[str, list[Path]] = {}
    for f in dest.glob("*.db"):
        if not _ours.match(f.name):
            continue
        stem = f.name.rsplit("_", 1)[0]
        by_stem.setdefault(stem, []).append(f)
    for stem, files in by_stem.items():
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[n_keep:]:
            try:
                old.unlink()
                log.info("归档清理：%s", old)
            except OSError as e:
                log.warning("归档清理失败 %s: %s", old, e)
    return created


async def run_archive_loop(dest_dir: str | None = None) -> None:
    """低频循环：每 interval 小时快照一次（lifespan 启动）。"""
    import asyncio

    log.info("本地归档任务已挂载")
    while True:
        try:
            archive_now(dest_dir=dest_dir)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("归档轮异常: %s", e)
        try:
            interval = float(get_settings().get("archive_interval_hours") or 24)
        except ValueError:
            interval = 24.0
        await asyncio.sleep(max(interval, 0.25) * 3600)
