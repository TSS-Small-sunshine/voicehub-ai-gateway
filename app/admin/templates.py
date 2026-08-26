"""VoiceHub AI Gateway — Jinja2 模板与静态资源。"""
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings
from ..providers.registry import PROVIDER_TEMPLATES

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env: Optional[Environment] = None


def jinja_env() -> Environment:
    global _env
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _env


def render(name: str, **ctx) -> HTMLResponse:
    template = jinja_env().get_template(name)
    body = template.render(provider_templates=PROVIDER_TEMPLATES, settings=settings, **ctx)
    return HTMLResponse(body)


def render_request(request: Request, name: str, **ctx) -> HTMLResponse:
    return render(name, request=request, **ctx)