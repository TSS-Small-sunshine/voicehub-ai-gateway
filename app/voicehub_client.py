"""VoiceHub AI Gateway — VoiceHub 主仓库 HTTP 客户端。

通过主仓库 API Key 体系（X-API-Key + ai-review:read/write 权限）拉待审 / 写回结果。
异常一律抛给调用方；上层确保失败降级为 REVIEW，绝不卡业务。
"""
import httpx

from .config import settings


class VoiceHubClient:
    """薄封装主仓库 /api/open/ai-review/* 两个端点。"""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def _headers(self) -> dict:
        return {"X-API-Key": settings.voicehub_api_key, "Content-Type": "application/json"}

    @property
    def base_url(self) -> str:
        return settings.voicehub_api_base_url.rstrip("/")

    async def fetch_pending(self, scene: str, limit: int = 20) -> list[dict]:
        """拉取指定场景的待审对象。"""
        resp = await self._client.get(
            f"{self.base_url}/api/open/ai-review/pending-list",
            params={"scene": scene, "limit": limit},
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    async def submit_result(
        self,
        scene: str,
        target_id: int,
        decision: str,
        reason: str | None,
        confidence: float | None,
        model: str | None,
        source: str | None,
        duration_ms: int | None,
    ) -> None:
        """写回审核结果到主仓库。"""
        body = {
            "scene": scene,
            "targetId": target_id,
            "decision": decision,
            "reason": reason,
            "confidence": confidence,
            "model": model,
            "source": source,
            "durationMs": duration_ms,
        }
        resp = await self._client.post(
            f"{self.base_url}/api/open/ai-review/result",
            json=body,
            headers=self._headers,
        )
        resp.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()