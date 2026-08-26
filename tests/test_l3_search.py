"""L3 搜索离线测试（强制无 Key / fake client）。"""
import pytest

from app.config import settings
from app.reviewers.l3_search import L3SearchReviewer


async def test_no_key_returns_empty_list(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "")
    r = L3SearchReviewer()
    assert await r.search("周杰伦 晴天 语言") == []


async def test_search_failure_returns_empty_list(monkeypatch):
    monkeypatch.setattr(settings, "tavily_api_key", "tvly-test")

    class BoomClient:
        async def search_async(self, **kwargs):
            raise RuntimeError("quota")

    r = L3SearchReviewer()
    r._client = BoomClient()
    assert await r.search("q") == []