"""LanguageDetector 离线测试（fake LLM / fake search，不触网）。"""

from app.reviewers.language_detector import LanguageDetector


class FakeLlm:
    """只实现 complete_json（detect 用到的接口）。"""

    def __init__(self, result):
        self._result = result

    async def complete_json(self, system_prompt, user_text):
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class FakeSearch:
    def __init__(self, results):
        self._results = results

    async def search(self, query, max_results=3):
        return self._results


async def test_platform_metadata_wins_no_llm_call():
    calls = []

    class TrackingLlm(FakeLlm):
        async def complete_json(self, system_prompt, user_text):
            calls.append(user_text)
            return await super().complete_json(system_prompt, user_text)

    d = LanguageDetector(TrackingLlm({"language": "英文"}), FakeSearch([]))
    r = await d.detect("晴天", "周杰伦", platform_language="中文")
    assert r == {"language": "中文", "confidence": 1.0, "source": "l1_platform"}
    assert calls == []


async def test_l2_confident_hit():
    llm = FakeLlm({"language": "日文", "confidence": 0.9})
    d = LanguageDetector(llm, FakeSearch([]))
    r = await d.detect("Lemon", "米津玄師")
    assert r["language"] == "日文" and r["source"] == "l2_llm" and r["confidence"] == 0.9


async def test_l2_low_confidence_triggers_l3():
    llm = FakeLlm({"language": "韩文", "confidence": 0.4})
    search = FakeSearch(["《X》 是韩文歌曲，由著名女团演唱。"])
    d = LanguageDetector(llm, search)
    r = await d.detect("X", "某团")
    assert r["language"] == "韩文" and r["source"] == "l3_search" and r["confidence"] == 0.5


async def test_l3_no_strong_signal_returns_unknown():
    llm = FakeLlm({"language": "英文", "confidence": 0.3})
    # 摘要有「中文」但无「X歌曲/X歌」等强信号 → 不得误判
    search = FakeSearch(["这是一篇中文报道，介绍了该歌曲的发行背景。"])
    d = LanguageDetector(llm, search)
    r = await d.detect("X", "Y")
    assert r["language"] == "" and r["source"] == "unknown"


async def test_l2_parse_failure_then_search():
    llm = FakeLlm(None)  # 解析失败
    search = FakeSearch(["语种：日文 的动画主题曲"])
    d = LanguageDetector(llm, search)
    r = await d.detect("X", "Y")
    assert r["language"] == "日文" and r["source"] == "l3_search"


async def test_l2_parse_failure_and_empty_search_returns_unknown():
    d = LanguageDetector(FakeLlm(None), FakeSearch([]))
    r = await d.detect("X", "Y")
    assert r["language"] == "" and r["source"] == "unknown"


async def test_unsafe_confidence_is_guarded():
    llm = FakeLlm({"language": "中文", "confidence": "不是数字"})
    d = LanguageDetector(llm, FakeSearch([]))
    r = await d.detect("X", "Y")
    # 置信度解析失败按 0 处理 → 走 L3 → 无信号 → unknown
    assert r["language"] == "" and r["source"] == "unknown"