"""L1 规则引擎：关键词黑名单 + 正则（零成本，本地判定）。

规则从 app/rules/ 目录加载；命中 REJECT，未命中返回 None 转 L2。
"""
import json
import re
from pathlib import Path

from ..config import settings


# 内置基础正则：确定性高、任何站点通用
# skip_scenes：该规则在这些场景下不生效（如注册场景剥离裸数字规则，防学号/纯数字用户名误杀）
DEFAULT_PATTERNS: list[dict] = [
    {"name": "phone_cn", "pattern": r"1[3-9]\d{9}", "label": "手机号", "skip_scenes": ["register"]},
    {"name": "qq_number", "pattern": r"(?<!\d)[1-9]\d{4,10}(?!\d)", "label": "疑似QQ号", "skip_scenes": ["register"]},
    {"name": "wechat_id", "pattern": r"(?i)(微信号?|vx|微信)[:：\s]*[a-zA-Z][-_a-zA-Z0-9]{5,19}", "label": "微信号"},
    {"name": "url", "pattern": r"https?://\S+|www\.\S+", "label": "URL链接"},
    {"name": "ad_keywords", "pattern": r"(加群|扫码|拉人|代理|刷赞|代刷|卖号|充值优惠|低价出)", "label": "引流话术"},
    {"name": "abuse", "pattern": r"(傻逼|去死|垃圾学校|废狗|操你|滚蛋)", "label": "辱骂"},
]


class L1RulesReviewer:
    """L1 规则审查：命中即 REJECT，未命中返回 None。

    review(text, scene)：scene 命中规则的 skip_scenes 时跳过该规则。
    """

    def __init__(self) -> None:
        self._patterns = self._load_rules()

    def _load_rules(self) -> list[tuple[str, re.Pattern, str, set]]:
        compiled = []
        for item in DEFAULT_PATTERNS:
            try:
                compiled.append(
                    (item["name"], re.compile(item["pattern"]), item["label"], set(item.get("skip_scenes") or []))
                )
            except re.error:
                continue  # 坏规则跳过，不影响启动

        # 可选的额外规则文件（json 数组，结构同 DEFAULT_PATTERNS）
        rules_file = Path(__file__).parent.parent / "rules" / "extra_patterns.json"
        if rules_file.exists():
            try:
                extra = json.loads(rules_file.read_text(encoding="utf-8"))
                for item in extra:
                    compiled.append(
                        (item["name"], re.compile(item["pattern"]), item["label"], set(item.get("skip_scenes") or []))
                    )
            except (json.JSONDecodeError, KeyError, re.error):
                pass
        return compiled

    async def review(self, text: str, scene: str | None = None) -> dict | None:
        """返回 {decision:'REJECT', reason, hit} 或 None（放行至 L2）。"""
        if not text:
            return None
        for name, pattern, label, skip in self._patterns:
            if scene and scene in skip:
                continue
            m = pattern.search(text)
            if m:
                return {
                    "decision": "REJECT",
                    "reason": f"命中L1规则「{label}」",
                    "source": "l1_rules",
                    "hit": name,
                }
        return None


# ---------------- 进程级单例（worker 与管理台共享；DB 规则热生效的唯一载体） ----------------
_runtime: L1RulesReviewer | None = None


def get_runtime() -> L1RulesReviewer:
    global _runtime
    if _runtime is None:
        _runtime = L1RulesReviewer()
    return _runtime


def reload_runtime() -> L1RulesReviewer:
    """重建实例：内置 + extra_patterns.json + gw_rules 表中启用的自定义规则。

    DB 读取失败（如测试库未建表）静默跳过仅用内置。调用方拿到同一份
    单例（进程内所有消费方 —— 管理台预览与轮询 worker —— 行为一致）。
    """
    global _runtime
    rt = L1RulesReviewer()
    try:
        from ..db import GatewaySession, GwRule

        session = GatewaySession()
        try:
            for r in session.query(GwRule).filter(GwRule.enabled.is_(True)).all():
                try:
                    skip = set(json.loads(r.skip_scenes_json or "[]"))
                except Exception:
                    skip = set()
                rt._patterns.append((r.name, re.compile(r.pattern), r.label, skip))
        finally:
            session.close()
    except Exception:
        pass
    _runtime = rt
    return rt