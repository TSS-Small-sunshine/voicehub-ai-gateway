"""VoiceHub AI Gateway — 供应商调用（含重试 / 熔断 / 主备切换）。"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI

from .config import settings
from .db import GatewaySession
from .providers import get_default_provider, list_providers
from .providers.service import decrypt_key

log = logging.getLogger("ai-gateway")

# 熔断：连续失败 N 次后切换备用；N 分钟后重试主供应商
FAIL_THRESHOLD = 3
COOLDOWN_SECONDS = 300


@dataclass
class _State:
    last_failure_at: float = 0.0
    consecutive_failures: int = 0


_state = _State()


def _build_client(provider) -> AsyncOpenAI:
    api_key = decrypt_key(provider.api_key_encrypted) or "no-key"
    return AsyncOpenAI(
        api_key=api_key,
        base_url=provider.base_url,
        timeout=provider.timeout_seconds or settings.llm_timeout_seconds,
        max_retries=0,  # 我们自己做指数退避
    )


def _candidate_providers() -> list:
    """候选供应商列表：默认（主）+ 其余已启用。"""
    session = GatewaySession()
    try:
        all_p = [p for p in list_providers(session) if p.enabled]
        default = get_default_provider(session)
        if not default:
            return all_p
        # 唯一：默认在前
        others = [p for p in all_p if p.id != default.id]
        return [default] + others
    finally:
        session.close()


async def _call_once(client: AsyncOpenAI, model: str, messages: list[dict], max_tokens: int) -> tuple[str, str]:
    """返回 (raw_content, model_actual)。"""
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.1,
        max_tokens=max_tokens,
    )
    content = (resp.choices[0].message.content or "").strip()
    return content, model


def _strip_fence(content: str) -> str:
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    return content


async def _try_provider(provider, system_prompt: str, user_text: str) -> Optional[dict]:
    """尝试单供应商；返回 {content, model} 或 None（已熔断/失败）。"""
    if _state.consecutive_failures >= FAIL_THRESHOLD and time.monotonic() - _state.last_failure_at < COOLDOWN_SECONDS:
        return None
    try:
        client = _build_client(provider)
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]
        # 3 次指数退避
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                content, model = await _call_once(client, provider.model, messages, provider.max_tokens)
                # 成功重置熔断
                _state.consecutive_failures = 0
                return {"content": _strip_fence(content), "model": model, "provider": provider.name}
            except Exception as e:
                last_err = e
                if attempt < 2:
                    # 异步上下文禁用阻塞 sleep：会卡死同进程 FastAPI event loop
                    await asyncio.sleep(delay)
                    delay *= 2
        log.warning("供应商 %s 重试 3 次仍失败：%s", provider.name, last_err)
        return None
    except Exception as e:
        log.warning("供应商 %s 构造/调用失败：%s", provider.name, e)
        return None


async def call_json(system_prompt: str, user_text: str) -> Optional[dict]:
    """主备切换调用；返回 {content, model, provider} 或 None（全部失败/已熔断）。"""
    candidates = _candidate_providers()
    if not candidates:
        return None
    for provider in candidates:
        out = await _try_provider(provider, system_prompt, user_text)
        if out:
            return out
        _state.consecutive_failures += 1
        _state.last_failure_at = time.monotonic()
    return None