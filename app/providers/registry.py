"""VoiceHub AI Gateway — 供应商预设模板（OpenAI 兼容）。

校方默认首选：小米 MiMo（计费参考：第三方审计估算；最终以下单页为准）。
"""
from __future__ import annotations

PROVIDER_TEMPLATES: list[dict] = [
    {
        "name": "小米 MiMo",
        "base_url": "https://api.xiaomi.com/v1",
        "model": "mimo-v2-5",
        "default": True,
        "note": "校方默认首选；OpenAI 兼容；不使用用户数据训练（官方承诺）。",
    },
    {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "default": False,
        "note": "OpenAI 兼容；按量便宜。",
    },
    {
        "name": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "default": False,
        "note": "OpenAI 兼容；长上下文友好。",
    },
    {
        "name": "通义千问（阿里）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "default": False,
        "note": "OpenAI 兼容模式。",
    },
    {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "default": False,
        "note": "OpenAI 兼容。",
    },
    {
        "name": "MiniMax",
        "base_url": "https://api.MiniMax.chat/v1",
        "model": "MiniMax-M3",
        "default": False,
        "note": "OpenAI 兼容。",
    },
    {
        "name": "硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "default": False,
        "note": "OpenAI 兼容；多家模型聚合。",
    },
    {
        "name": "本地 Ollama（数据零出校）",
        "base_url": "http://host.docker.internal:11434/v1",
        "model": "qwen2.5:3b",
        "default": False,
        "note": "CPU 推理（兆芯 AVX2 也可运行 1.5B-3B Q4_K_M）；推荐 ≥16GB 内存；超时建议 60-120s。",
    },
]