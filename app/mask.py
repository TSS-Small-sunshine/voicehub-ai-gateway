"""VoiceHub AI Gateway — PII 脱敏（送审/落盘唯一关口）。

规则（顺序）：
- URL：http(s)://... 与 www.... → [URL]
- 手机号：1[3-9]xxxxxxxxx → 138****5678
- 身份证：18 位 → 前 4 + ******** + 后 4
- QQ：[1-9]\d{4,10}（5–11 位数字）→ 前 3 + 后 2（其它位 *）
- 学号：≥6 位连续数字（QQ 未命中部分）→ 前 2 + 后 2

姓名/学号字段级脱敏：见 mask_field_name / mask_field_student_no（在文本片段上下文使用），
不在通用 mask_pii 中做（避免误伤短语）。

用户内容（不可信数据）按字符串函数处理。
"""
from __future__ import annotations

import re

_PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")
_IDCARD_RE = re.compile(r"\b\d{17}[\dXx]\b")
_URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)

# QQ：5–11 位，首位 1–9
_QQ_RE = re.compile(r"(?<!\d)[1-9]\d{4,10}(?!\d)")
# 学号：6–12 位连续数字
_STUDENTNO_RE = re.compile(r"(?<!\d)\d{6,12}(?!\d)")


def mask_url(text: str) -> str:
    return _URL_RE.sub("[URL]", text)


def mask_phone(text: str) -> str:
    return _PHONE_RE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], text)


def mask_idcard(text: str) -> str:
    return _IDCARD_RE.sub(lambda m: m.group(0)[:4] + "**********" + m.group(0)[-4:], text)


def mask_qq(text: str) -> str:
    def _sub(m):
        s = m.group(0)
        if len(s) <= 5:
            return s
        return s[:3] + "*" * (len(s) - 5) + s[-2:]
    return _QQ_RE.sub(_sub, text)


def mask_student_no(text: str) -> str:
    """QQ 已先处理；本函数处理剩余 6+ 位数字。"""
    def _sub(m):
        s = m.group(0)
        if len(s) <= 4:
            return s
        return s[:2] + "*" * (len(s) - 4) + s[-2:]
    return _STUDENTNO_RE.sub(_sub, text)


def mask_pii(text: str) -> str:
    """通用入口：联系方式/学号/URL 脱敏。"""
    if not text:
        return text
    text = mask_url(text)
    text = mask_phone(text)
    text = mask_idcard(text)
    text = mask_qq(text)
    text = mask_student_no(text)
    return text


# 字段级（业务上下文确定是姓名时调用）
def mask_field_name(name: str) -> str:
    """姓名字段：保留首字 + 星号；空/单字不动。"""
    if not name:
        return name
    s = name.strip()
    if len(s) <= 1:
        return s
    return s[0] + "*" * (len(s) - 1)


def mask_field_student_no(no: str) -> str:
    """学号字段：≥6 位数字时前 2 + 后 2。"""
    if not no:
        return no
    s = no.strip()
    if len(s) < 6:
        return s
    return s[:2] + "*" * (len(s) - 4) + s[-2:]