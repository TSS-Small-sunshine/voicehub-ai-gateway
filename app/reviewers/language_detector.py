"""歌曲语种检测：三级数据源（平台元数据 → LLM → 搜索）。

L1 平台元数据由主仓在 payload 中给出（网易云/QQ language 字段），零成本；
L2 LLM 判标题+歌手语种（独立 JSON schema：language + confidence，不经 decision 校验）；
L3 搜索仅当 L2 低置信（<0.6）时兜底，只认「X歌曲/X歌/语种：X」类强信号。
"""
from .l2_llm import L2LlmReviewer
from .l3_search import L3SearchReviewer

LANGUAGE_SYSTEM_PROMPT = (
    "你是歌曲语种识别助手。根据歌名与歌手判断歌曲主要语言。"
    "只输出 JSON：{\"language\":\"中文|英文|日文|韩文|粤语|其他\",\"confidence\":0~1}"
)

ALLOWED_LANGUAGES: list[str] = ["中文", "英文", "日文", "韩文", "粤语", "其他"]


class LanguageDetector:
    """三级语种判定。"""

    def __init__(self, llm: L2LlmReviewer, search: L3SearchReviewer | None) -> None:
        self._llm = llm
        self._search = search  # None = 运行期关闭 L3（管理台设置 l3_enabled）

    async def detect(
        self,
        title: str,
        artist: str,
        platform_language: str | None = None,
    ) -> dict:
        """返回 {language, confidence, source}；无法判定 → {language:'', confidence:0, source:'unknown'}。"""
        # L1：平台元数据
        if platform_language and platform_language.lower() in ("中文", "chinese", "cn", "普通话", "粤语"):
            return {"language": platform_language, "confidence": 1.0, "source": "l1_platform"}

        # L2：LLM（独立 schema，不经过 decision 校验）
        data = await self._llm.complete_json(
            LANGUAGE_SYSTEM_PROMPT, f"歌名：《{title}》 歌手：{artist}"
        )
        lang = str(data.get("language") or "").strip() if data else ""
        confidence = self._safe_confidence(data.get("confidence") if data else None) if lang else 0.0
        if lang in ALLOWED_LANGUAGES and confidence >= 0.6:
            return {"language": lang, "confidence": confidence, "source": "l2_llm"}

        # L3：搜索兜底（仅 L2 低置信/失败时）
        if confidence < 0.6:
            hit = await self._search_fallback(title, artist)
            if hit:
                return hit

        return {"language": "", "confidence": 0.0, "source": "unknown"}

    @staticmethod
    def _safe_confidence(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    async def _search_fallback(self, title: str, artist: str) -> dict | None:
        if self._search is None:
            return None
        summaries = await self._search.search(f"{title} {artist} 歌曲 语言")
        blob = " ".join(summaries)
        for guess in ALLOWED_LANGUAGES:
            if any(
                marker in blob
                for marker in (f"{guess}歌曲", f"{guess}歌", f"语种：{guess}", f"语言：{guess}")
            ):
                return {"language": guess, "confidence": 0.5, "source": "l3_search"}
        return None