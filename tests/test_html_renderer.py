"""Tests for HtmlDetailRenderer and StorageManager.save_html."""

from collections import OrderedDict
from datetime import datetime, timezone

from src.models import ContentItem, SourceType
from src.renderers.html_detail import HtmlDetailRenderer
from src.storage.manager import StorageManager


def _make_item(
    title="Test Item",
    score=8.5,
    url="https://example.com",
    published="2026-03-12T10:00:00Z",
    **meta,
):
    return ContentItem(
        id="test:1",
        source_type=SourceType.RSS,
        title=title,
        url=url,
        published_at=published,
        ai_score=score,
        ai_tags=meta.pop("ai_tags", ["ai", "ml"]),
        ai_summary=meta.pop("ai_summary", None),
        metadata=meta,
    )


def test_render_basic():
    renderer = HtmlDetailRenderer()
    items = {"头条速递": [_make_item()]}
    html = renderer.render(items, "2026-03-12", 100)
    assert "<!DOCTYPE html>" in html
    assert "HORIZON 日报" in html
    assert "头条速递" in html
    assert "2026-03-12" in html
    assert "Test Item" in html


def test_render_score_badge_classes():
    renderer = HtmlDetailRenderer()
    items = {
        "G": [
            _make_item(title="High", score=9.0),
            _make_item(title="Mid", score=7.5),
            _make_item(title="Low", score=6.0),
        ]
    }
    html = renderer.render(items, "d", 10)
    assert "score-high" in html
    assert "score-mid" in html
    assert "score-low" in html


def test_render_no_score():
    renderer = HtmlDetailRenderer()
    item = _make_item(score=None)
    html = renderer.render({"G": [item]}, "d", 10)
    body_html = html.split("</style>", 1)[-1]
    assert "score-badge" not in body_html


def test_render_empty_group_skipped():
    renderer = HtmlDetailRenderer()
    html = renderer.render({"Empty": [], "Full": [_make_item()]}, "d", 10)
    assert "Empty" not in html
    assert "Full" in html


def test_render_multiple_groups_order():
    renderer = HtmlDetailRenderer()
    groups = OrderedDict([
        ("头条速递", [_make_item(title="H1")]),
        ("关注动态", [_make_item(title="W1", score=None)]),
    ])
    html = renderer.render(groups, "d", 10)
    h_pos = html.index("头条速递")
    w_pos = html.index("关注动态")
    assert h_pos < w_pos


def test_prepare_item_fallback_chain():
    renderer = HtmlDetailRenderer()

    item_full = _make_item(
        title_zh="中文标题",
        detailed_summary_zh="中文摘要",
        background_zh="中文背景",
        community_discussion_zh="社区讨论",
    )
    d = renderer._prepare_item(item_full)
    assert d["title"] == "中文标题"
    assert d["summary"] == "中文摘要"
    assert d["background"] == "中文背景"
    assert d["discussion"] == "社区讨论"

    item_en = _make_item(
        detailed_summary="English summary",
        background="English bg",
    )
    d2 = renderer._prepare_item(item_en)
    assert d2["summary"] == "English summary"
    assert d2["background"] == "English bg"

    item_bare = _make_item(ai_summary="AI sum")
    d3 = renderer._prepare_item(item_bare)
    assert d3["summary"] == "AI sum"

    item_empty = _make_item()
    d4 = renderer._prepare_item(item_empty)
    assert d4["summary"] == ""
    assert d4["background"] == ""


def test_prepare_item_source_line():
    renderer = HtmlDetailRenderer()
    item = _make_item(feed_name="Simon Willison")
    d = renderer._prepare_item(item)
    assert "rss" in d["source_line"]
    assert "Simon Willison" in d["source_line"]


def test_prepare_item_tags():
    renderer = HtmlDetailRenderer()
    item = _make_item(ai_tags=["ai", "llm"])
    d = renderer._prepare_item(item)
    assert d["tags"] == ["ai", "llm"]


def test_stats_counts():
    renderer = HtmlDetailRenderer()
    items = {"A": [_make_item()] * 3, "B": [_make_item()] * 2}
    html = renderer.render(items, "d", 50)
    assert ">50<" in html.replace(" ", "").replace("\n", "") or "50" in html
    assert "5" in html


def test_save_html(tmp_path):
    sm = StorageManager(data_dir=str(tmp_path))
    path = sm.save_html("2026-03-12", "<html>test</html>")
    assert path.exists()
    assert path.name == "horizon-2026-03-12.html"
    assert path.parent.name == "html"
    assert path.read_text(encoding="utf-8") == "<html>test</html>"
