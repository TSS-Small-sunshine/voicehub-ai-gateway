"""歌曲语种检测：三级数据源（平台元数据 → LLM → 搜索）。

L1 平台元数据由主仓在 payload 中给出（网易云/QQ language 字段），零成本；
L2 LLM 判标题+歌手语种；
L3 搜索仅当 L2 低置信（<0.6）且为可选场景（语种判定）时兜底。
"""
from .l2_llm import L2LlmReviewer
from .l3_search import L3SearchReviewer

LANGUAGE_SYSTEM_PROMPT = (
    "你是歌曲语种识别助手。根据歌名与歌手判断歌曲主要语言。"
    "只输出 JSON：{\"language\":\"中文|英文|日文|韩文|粤语|其他\",\"confidence\":0~1}"
)

ALLOWED_LANGUAGES: list[str] = ["中文", "英文", "日文", "韩文", "粤语"]


class LanguageDetector:
    """三级语种判定。"""

    def __init__(self, llm: L2LlmReviewer, search: L3SearchReviewer) -> None:
        self._llm = llm
        self._search = search

    async def detect(
        self,
        title: str,
        artist: str,
        platform_language: str | None = None,
    ) -> dict:
        """返回 {language, confidence, source}；无法判定 → {source:'l1_platform', confidence:0}。"""
        # L1：平台元数据
        if platform_language and platform_language.lower() in ("中文", "chinese", "cn", "普通话", "粤语"):
            return {"language": platform_language, "confidence": 1.0, "source": "l1_platform"}

        # L2：LLM
        result = await self._llm.review(
            LANGUAGE_SYSTEM_PROMPT, f"歌名：《{title}》 歌手：{artist}"
        )
        lang = (result.get("reason") or "").strip()
        confidence = float(result.get("confidence", 0.0))
        if lang in ALLOWED_LANGUAGES and confidence >= 0.6:
            return {"language": lang, "confidence": confidence, "source": result.get("source", "l2_llm")}

        # L3：搜索兜底（仅低置信且未配置则不搜）
        if confidence < 0.6:
            summaries = await self._search.search(f"{title} {artist} 歌曲 语言")
            # TODO: 从摘要中简单提取语言关键词（Phase 3 细化）
            blob = " ".join(summaries)
            for guess in ALLOWED_LANGUAGES:
                if guess in blob:
                    return {"language": guess, "confidence": 0.5, "source": "l3_search"}

        return {"language": "", "confidence": 0.0, "source": "unknown"}