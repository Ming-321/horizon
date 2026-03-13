"""Tests for BriefRenderer and StorageManager.save_brief."""

import tempfile
from collections import OrderedDict
from pathlib import Path

from src.models import ContentItem, SourceType
from src.renderers.brief import BriefRenderer, _first_sentence
from src.storage.manager import StorageManager


def _make_item(title="Test", score=8.5, url="https://example.com", **meta):
    return ContentItem(
        id="test:1",
        source_type=SourceType.RSS,
        title=title,
        url=url,
        published_at="2026-03-12T00:00:00Z",
        ai_score=score,
        metadata=meta,
    )


def test_render_basic_group():
    items = [_make_item(title="Item A", score=9.0), _make_item(title="Item B", score=8.0)]
    renderer = BriefRenderer(top_n=10)
    md = renderer.render({"头条速递": items}, "2026-03-12")
    assert "# Horizon 每日速递 - 2026-03-12" in md
    assert "## 头条速递" in md
    assert "⭐ 9.0" in md
    assert "[Item A]" in md


def test_render_top_n_limit():
    items = [_make_item(title=f"Item {i}", score=10 - i) for i in range(15)]
    renderer = BriefRenderer(top_n=3)
    md = renderer.render({"Test": items}, "2026-03-12")
    assert "3. " in md
    assert "4. " not in md


def test_render_empty_group_skipped():
    renderer = BriefRenderer()
    md = renderer.render({"Empty": [], "HasItems": [_make_item()]}, "2026-03-12")
    assert "## Empty" not in md
    assert "## HasItems" in md


def test_render_no_score():
    item = _make_item(score=None)
    renderer = BriefRenderer()
    md = renderer.render({"Watched": [item]}, "2026-03-12")
    assert "⭐" not in md
    assert "[Test]" in md


def test_title_zh_priority():
    item = _make_item(title="English Title", title_zh="中文标题", detailed_summary_zh="摘要内容。")
    renderer = BriefRenderer()
    md = renderer.render({"G": [item]}, "2026-03-12")
    assert "中文标题" in md
    assert "English Title" not in md


def test_summary_fallback_chain():
    item_zh = _make_item(detailed_summary_zh="中文摘要。后续内容。")
    renderer = BriefRenderer()
    md = renderer.render({"G": [item_zh]}, "d")
    assert "中文摘要。后续内容。" in md

    item_en = _make_item(detailed_summary="English summary. More.")
    md2 = renderer.render({"G": [item_en]}, "d")
    assert "English summary. More." in md2

    item_ai = ContentItem(
        id="t:1", source_type=SourceType.RSS, title="T",
        url="https://x.com", published_at="2026-01-01T00:00:00Z",
        ai_score=5.0, ai_summary="AI generated summary.",
    )
    md3 = renderer.render({"G": [item_ai]}, "d")
    assert "AI generated summary." in md3


def test_summary_full_text_preserved():
    long_text = "A" * 100
    item = _make_item(detailed_summary_zh=long_text)
    renderer = BriefRenderer()
    md = renderer.render({"G": [item]}, "d")
    assert long_text in md


def test_first_sentence_empty():
    assert _first_sentence("") == ""


def test_first_sentence_no_punctuation():
    assert _first_sentence("Hello world no period", max_len=10) == "Hello worl…"


def test_first_sentence_chinese():
    assert _first_sentence("这是第一句。这是第二句。") == "这是第一句。"


def test_save_brief(tmp_path):
    sm = StorageManager(data_dir=str(tmp_path))
    path = sm.save_brief("2026-03-12", "# Brief")
    assert path.exists()
    assert path.name == "horizon-2026-03-12-brief.md"
    assert path.read_text(encoding="utf-8") == "# Brief"


def test_multiple_groups_ordered():
    groups = OrderedDict([
        ("头条速递", [_make_item(title="H1", score=9.0)]),
        ("关注动态", [_make_item(title="W1", score=None)]),
        ("GitHub 热榜", [_make_item(title="G1", score=8.0)]),
    ])
    renderer = BriefRenderer()
    md = renderer.render(groups, "2026-03-12")
    h_pos = md.index("## 头条速递")
    w_pos = md.index("## 关注动态")
    g_pos = md.index("## GitHub 热榜")
    assert h_pos < w_pos < g_pos
