"""L1 规则引擎：关键词黑名单 + 正则（零成本，本地判定）。

规则从 app/rules/ 目录加载；命中 REJECT，未命中返回 None 转 L2。
"""
import json
import re
from pathlib import Path

from ..config import settings


# 内置基础正则：确定性高、任何站点通用
DEFAULT_PATTERNS: list[dict] = [
    {"name": "phone_cn", "pattern": r"1[3-9]\d{9}", "label": "手机号"},
    {"name": "qq_number", "pattern": r"(?<!\d)[1-9]\d{4,10}(?!\d)", "label": "疑似QQ号"},
    {"name": "wechat_id", "pattern": r"(?i)(微信号?|vx|微信)[:：\s]*[a-zA-Z][-_a-zA-Z0-9]{5,19}", "label": "微信号"},
    {"name": "url", "pattern": r"https?://\S+|www\.\S+", "label": "URL链接"},
    {"name": "ad_keywords", "pattern": r"(加群|扫码|拉人|代理|刷赞|代刷|卖号|充值优惠|低价出)", "label": "引流话术"},
    {"name": "abuse", "pattern": r"(傻逼|去死|垃圾学校|废狗|操你|滚蛋)", "label": "辱骂"},
]


class L1RulesReviewer:
    """L1 规则审查：命中即 REJECT，未命中返回 None。"""

    def __init__(self) -> None:
        self._patterns = self._load_rules()

    def _load_rules(self) -> list[tuple[str, re.Pattern, str]]:
        compiled = []
        for item in DEFAULT_PATTERNS:
            try:
                compiled.append((item["name"], re.compile(item["pattern"]), item["label"]))
            except re.error:
                continue  # 坏规则跳过，不影响启动

        # 可选的额外规则文件（json 数组，结构同 DEFAULT_PATTERNS）
        rules_file = Path(__file__).parent.parent / "rules" / "extra_patterns.json"
        if rules_file.exists():
            try:
                extra = json.loads(rules_file.read_text(encoding="utf-8"))
                for item in extra:
                    compiled.append((item["name"], re.compile(item["pattern"]), item["label"]))
            except (json.JSONDecodeError, KeyError, re.error):
                pass
        return compiled

    async def review(self, text: str) -> dict | None:
        """返回 {decision:'REJECT', reason, hit} 或 None（放行至 L2）。"""
        if not text:
            return None
        for name, pattern, label in self._patterns:
            m = pattern.search(text)
            if m:
                return {
                    "decision": "REJECT",
                    "reason": f"命中L1规则「{label}」",
                    "source": "l1_rules",
                    "hit": name,
                }
        return None