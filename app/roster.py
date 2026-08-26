"""VoiceHub AI Gateway — 名册服务（CSV 导入 + 学号 HMAC + 注册备注比对）。

一期约定（SPEC [S12]）：
- 身份锚点为学号（HMAC-SHA256 存储密钥来自 ADMIN_SECRET，不落明文）；
- 比对在网关内完成：注册场景 L1/L2 判定 APPROVE 后，若备注可提取出候选学号：
  命中名册且与注册姓名一致 → 放行；命中但姓名不一致 / 未命中（疑编造）→ REVIEW 转人工；
- 名册未导入或备注无候选学号 → 不干预（保持既有行为）；
- 比对结果不外显，绝不自动写回主仓删号。
"""
from __future__ import annotations

import csv
import io
import re

from sqlalchemy.orm import Session

from .db import GwRoster
from .mask import mask_field_student_no
from .security import hmac_student_no

MAX_CSV_BYTES = 5 * 1024 * 1024  # ≤5MB
REQUIRED_HEADERS = {"学号", "姓名"}
_MAX_ROWS = 20000

_NO_RE = re.compile(r"(?<!\d)(\d{6,12})(?!\d)")


class RosterImportError(ValueError):
    """CSV 校验失败（消息可直接展示给管理员）。"""


# ---------------- 导入 ----------------
def parse_roster_csv(data: bytes) -> list[dict]:
    """解析并校验 CSV → [{"student_no","name","grade","class"}]；任何问题抛 RosterImportError。"""
    if len(data) > MAX_CSV_BYTES:
        raise RosterImportError("文件超过 5MB 上限")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise RosterImportError("编码须为 UTF-8") from e

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [f.strip() for f in (reader.fieldnames or [])]
    missing = REQUIRED_HEADERS - set(fieldnames)
    if missing:
        raise RosterImportError(f"列头缺少：{'、'.join(sorted(missing))}（需要：学号、姓名，可选：年级、班级）")

    rows: list[dict] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(reader, start=2):
        def cell(key: str) -> str:
            val = raw.get(key)
            return str(val).strip() if val is not None else ""

        student_no, name = cell("学号"), cell("姓名")
        grade, class_ = cell("年级")[:32], cell("班级")[:32]
        if not student_no or not name:
            raise RosterImportError(f"第 {lineno} 行学号或姓名为空")
        if not _NO_RE.fullmatch(student_no):
            raise RosterImportError(f"第 {lineno} 行学号格式异常（应为 6-12 位数字）：{mask_field_student_no(student_no)}")
        if len(name) > 64:
            raise RosterImportError(f"第 {lineno} 行姓名过长")
        if student_no in seen:
            raise RosterImportError(f"第 {lineno} 行学号与第 {seen[student_no]} 行重复")
        seen[student_no] = lineno
        rows.append({"student_no": student_no, "name": name, "grade": grade, "class": class_})
        if len(rows) >= _MAX_ROWS:
            raise RosterImportError(f"超出单次导入上限 {_MAX_ROWS} 行")
    if not rows:
        raise RosterImportError("未解析到数据行")
    return rows


def import_rows(session: Session, rows: list[dict], actor: str) -> tuple[int, int]:
    """按 HMAC 幂等覆盖写库，返回 (新增, 更新)。调用方负责审计留痕。"""
    added = updated = 0
    for r in rows:
        digest = hmac_student_no(r["student_no"])
        existing = session.query(GwRoster).filter(GwRoster.student_no_hmac == digest).one_or_none()
        if existing is None:
            session.add(GwRoster(
                student_no_hmac=digest,
                name=r["name"],
                grade=r["grade"] or None,
                class_=r["class"] or None,
                imported_by=actor,
            ))
            added += 1
        else:
            existing.name = r["name"]
            existing.grade = r["grade"] or None
            existing.class_ = r["class"] or None
            existing.imported_by = actor
            updated += 1
    session.commit()
    return added, updated


def roster_size() -> int:
    from .db import GatewaySession
    session = GatewaySession()
    try:
        return session.query(GwRoster).count()
    finally:
        session.close()


def list_roster(limit: int = 500) -> list[GwRoster]:
    from .db import GatewaySession
    session = GatewaySession()
    try:
        return session.query(GwRoster).order_by(GwRoster.id.desc()).limit(limit).all()
    finally:
        session.close()


# ---------------- 备注比对 ----------------
def extract_student_no(remark: str | None) -> str | None:
    """从备注中提取疑似学号（6-12 位独立数字串，取首个）。"""
    m = _NO_RE.search(remark or "")
    return m.group(1) if m else None


def check_register_note(payload: dict) -> tuple[bool, str]:
    """注册备注实名比对（SPEC [S12] 一期）。

    返回 (是否通过, 拒因)。名册为空或备注无候选学号时不干预。
    """
    from .db import GatewaySession
    session = GatewaySession()
    try:
        if session.query(GwRoster).count() == 0:
            return True, ""
        remark = str(payload.get("remark") or "")
        candidate = extract_student_no(remark)
        if not candidate:
            return True, ""
        entry = (
            session.query(GwRoster)
            .filter(GwRoster.student_no_hmac == hmac_student_no(candidate))
            .one_or_none()
        )
        reg_name = str(payload.get("name") or "").strip()
        if entry is None:
            return False, f"备注学号 {mask_field_student_no(candidate)} 不在名册（疑编造），转人工"
        if not reg_name or reg_name != entry.name:
            return False, f"备注学号 {mask_field_student_no(candidate)} 与名册姓名不一致，转人工"
        return True, ""
    finally:
        session.close()
