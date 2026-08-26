"""L3 搜索审查：Tavily / SearXNG（可选，语种等低置信兜底）。

未配置 TAVILY_API_KEY 时自动跳过（返回 None → 上层保持 REVIEW）。
"""
from ..config import settings


class L3SearchReviewer:
    """结构化搜索：按次调用，仅当 L2 低置信且 L1/L2 均为定语种时由上层触发。"""

    def __init__(self) -> None:
        self._client = None
        if settings.tavily_api_key:
            try:
                from tavily import TavilyClient  # 可选依赖，导入失败不阻塞启动

                self._client = TavilyClient(api_key=settings.tavily_api_key)
            except ImportError:
                self._client = None

    async def search(self, query: str, max_results: int = 3) -> list[str]:
        """返回搜索结果摘要列表；未配置/失败返回空列表。"""
        if not self._client:
            return []
        try:
            result = await self._client.search_async(query=query, max_results=max_results)
            return [r.get("content", "") for r in result.get("results", [])]
        except Exception:
            return []