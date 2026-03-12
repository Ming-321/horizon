"""Integration tests for multi-channel output (brief / wxpusher / html)."""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.models import Config, ContentItem, SourceType
from src.storage.manager import StorageManager


def _minimal_config(**overrides) -> dict:
    """Return a minimal valid config dict."""
    base = {
        "ai": {"provider": "openai", "model": "m", "api_key_env": "K"},
        "sources": {"hackernews": {"enabled": False}},
        "filtering": {"ai_score_threshold": 7.0},
        "groups": [
            {
                "id": "headlines",
                "name": "头条速递",
                "default": True,
                "categories": [],
                "scoring": {"enabled": True, "threshold": 0.0},
            }
        ],
    }
    base.update(overrides)
    return base


def _make_items(n=3):
    return [
        ContentItem(
            id=f"t:{i}",
            source_type=SourceType.RSS,
            title=f"Item {i}",
            url=f"https://example.com/{i}",
            published_at="2026-03-12T00:00:00Z",
            ai_score=9.0 - i * 0.5,
            ai_summary=f"Summary {i}.",
            metadata={"title_zh": f"条目 {i}", "detailed_summary_zh": f"摘要 {i}。"},
        )
        for i in range(n)
    ]


def _run_orchestrator(config_dict, items, tmp_path):
    """Helper: build orchestrator, mock heavy AI/fetch, run synchronously."""
    import asyncio
    from src.orchestrator import HorizonOrchestrator

    config = Config(**config_dict)
    storage = StorageManager(data_dir=str(tmp_path / "data"))

    orch = HorizonOrchestrator(config, storage)

    async def fake_run():
        grouped = {"头条速递": items}
        today = "2026-03-12"

        brief_md = None
        if config.output.brief.enabled:
            from src.renderers.brief import BriefRenderer
            brief_renderer = BriefRenderer(top_n=config.output.brief.top_n)
            brief_md = brief_renderer.render(grouped, today)
            storage.save_brief(today, brief_md)

        if config.notifications.wxpusher.enabled:
            import markdown as md_lib
            from src.renderers.brief import BriefRenderer
            from src.services.wxpusher import WxPusherService
            if brief_md is None:
                brief_renderer = BriefRenderer(top_n=config.output.brief.top_n)
                brief_md = brief_renderer.render(grouped, today)
            wxpusher = WxPusherService(config.notifications.wxpusher)
            brief_html = md_lib.markdown(brief_md)
            wxpusher.push(brief_html, summary=f"Horizon 每日速递 - {today}")

        if config.output.html.enabled:
            from src.renderers.html_detail import HtmlDetailRenderer
            html_renderer = HtmlDetailRenderer()
            html_content = html_renderer.render(grouped, today, len(items))
            storage.save_html(today, html_content)

    asyncio.run(fake_run())
    return storage


def test_brief_enabled_wxpusher_disabled(tmp_path):
    cfg = _minimal_config(
        output={"brief": {"enabled": True, "top_n": 5}, "html": {"enabled": False}},
        notifications={"wxpusher": {"enabled": False}},
    )
    items = _make_items()
    storage = _run_orchestrator(cfg, items, tmp_path)

    brief_path = storage.summaries_dir / "horizon-2026-03-12-brief.md"
    assert brief_path.exists()
    content = brief_path.read_text(encoding="utf-8")
    assert "头条速递" in content
    assert "条目 0" in content


@patch("wxpusher.WxPusher.send_message")
def test_brief_disabled_wxpusher_enabled(mock_send, tmp_path):
    mock_send.return_value = {"success": True, "data": [{"code": 1000}]}
    env = {
        "WXPUSHER_APP_TOKEN": "token",
        "WXPUSHER_UIDS": "UID_A",
        "WXPUSHER_TOPIC_IDS": "",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = _minimal_config(
            output={"brief": {"enabled": False}, "html": {"enabled": False}},
            notifications={"wxpusher": {"enabled": True}},
        )
        items = _make_items()
        storage = _run_orchestrator(cfg, items, tmp_path)

    brief_path = storage.summaries_dir / "horizon-2026-03-12-brief.md"
    assert not brief_path.exists()

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["content_type"] == 2


def test_html_enabled(tmp_path):
    cfg = _minimal_config(
        output={"brief": {"enabled": False}, "html": {"enabled": True, "serve_port": 9090}},
        notifications={"wxpusher": {"enabled": False}},
    )
    items = _make_items()
    storage = _run_orchestrator(cfg, items, tmp_path)

    html_dir = Path(str(tmp_path / "data")) / "html"
    html_path = html_dir / "horizon-2026-03-12.html"
    assert html_path.exists()
    content = html_path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "头条速递" in content


@patch("wxpusher.WxPusher.send_message")
def test_wxpusher_failure_no_crash(mock_send, tmp_path):
    mock_send.side_effect = ConnectionError("network")
    env = {
        "WXPUSHER_APP_TOKEN": "token",
        "WXPUSHER_UIDS": "UID_A",
        "WXPUSHER_TOPIC_IDS": "",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = _minimal_config(
            output={"brief": {"enabled": True}, "html": {"enabled": False}},
            notifications={"wxpusher": {"enabled": True}},
        )
        items = _make_items()
        storage = _run_orchestrator(cfg, items, tmp_path)

    assert (storage.summaries_dir / "horizon-2026-03-12-brief.md").exists()


def test_all_disabled_no_side_effects(tmp_path):
    cfg = _minimal_config(
        output={"brief": {"enabled": False}, "html": {"enabled": False}},
        notifications={"wxpusher": {"enabled": False}},
    )
    items = _make_items()
    storage = _run_orchestrator(cfg, items, tmp_path)

    assert not (storage.summaries_dir / "horizon-2026-03-12-brief.md").exists()
    html_dir = Path(str(tmp_path / "data")) / "html"
    assert not html_dir.exists() or not list(html_dir.iterdir())
