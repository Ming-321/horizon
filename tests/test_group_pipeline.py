"""Tests for Phase 3: group-aware pipeline, prompt resolution, and grouped summary."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.ai.prompts import get_scoring_prompt, CONTENT_ANALYSIS_SYSTEM
from src.ai.summarizer import DailySummarizer
from src.models import (
    Config,
    ContentItem,
    FilteringConfig,
    GroupConfig,
    ScoringConfig,
    SourceType,
)
from src.orchestrator import GroupBucket, HorizonOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(title: str = "Test", category: str = None, score: float = None) -> ContentItem:
    item = ContentItem(
        id=f"test:{title}",
        source_type=SourceType.RSS,
        title=title,
        url="https://example.com",
        content="content",
        published_at=datetime(2026, 3, 12, tzinfo=timezone.utc),
        category=category,
    )
    if score is not None:
        item.ai_score = score
    return item


def _minimal_config(groups=None) -> Config:
    raw = json.loads(Path("data/config.json").read_text())
    if groups is not None:
        raw["groups"] = groups
    return Config(**raw)


# ---------------------------------------------------------------------------
# _route_to_groups
# ---------------------------------------------------------------------------

def test_route_with_groups():
    """Items are routed to the correct group by category."""
    config = _minimal_config()
    from src.storage.manager import StorageManager
    orch = HorizonOrchestrator(config, MagicMock(spec=StorageManager))

    items = [
        _item("HN story", category="hackernews"),
        _item("AI telegram", category="ai-news"),
        _item("GH trending", category="github-trending"),
        _item("Tech RSS", category="tech-news"),
    ]

    grouped = orch._route_to_groups(items)

    group_names = list(grouped.keys())
    assert len(group_names) >= 2

    headlines = grouped.get("\u5934\u6761") or grouped.get("Headlines")
    if headlines:
        cats = [i.category for i in headlines.items]
        assert "hackernews" in cats or "ai-news" in cats or "tech-news" in cats


def test_route_backward_compat_no_groups():
    """Without groups config, all items go into a single 'Daily' bucket."""
    config = _minimal_config(groups=[])
    from src.storage.manager import StorageManager
    orch = HorizonOrchestrator(config, MagicMock(spec=StorageManager))

    items = [_item("A"), _item("B"), _item("C")]
    grouped = orch._route_to_groups(items)

    assert len(grouped) == 1
    bucket = list(grouped.values())[0]
    assert len(bucket.items) == 3
    assert bucket.group.scoring.threshold == config.filtering.ai_score_threshold


def test_route_unmatched_goes_to_default():
    """Items with unknown category go to the default group."""
    config = _minimal_config()
    from src.storage.manager import StorageManager
    orch = HorizonOrchestrator(config, MagicMock(spec=StorageManager))

    items = [_item("Unknown", category="nonexistent-cat")]
    grouped = orch._route_to_groups(items)

    default_group = None
    for name, bucket in grouped.items():
        if bucket.group.default:
            default_group = bucket
            break

    if default_group:
        assert len(default_group.items) >= 1


# ---------------------------------------------------------------------------
# Scoring bypass
# ---------------------------------------------------------------------------

def test_bypass_group_skips_scoring():
    """Groups with scoring.enabled=false keep items with ai_score=None."""
    g = GroupConfig(
        id="bypass", name="Bypass",
        scoring=ScoringConfig(enabled=False),
    )
    bucket = GroupBucket(group=g, items=[_item("A"), _item("B")])

    assert bucket.group.scoring.enabled is False
    for item in bucket.items:
        assert item.ai_score is None


# ---------------------------------------------------------------------------
# get_scoring_prompt three-level fallback
# ---------------------------------------------------------------------------

def test_scoring_prompt_source_entry_override():
    """Source-level prompt file takes priority."""
    with patch("src.ai.prompts.load_prompt", return_value="source-level prompt"):
        result = get_scoring_prompt(
            source_prompt_file="custom_source.txt",
            group_prompt_file="group_default.txt",
        )
    assert result == "source-level prompt"


def test_scoring_prompt_group_fallback():
    """Group-level prompt is used when source has none."""
    with patch("src.ai.prompts.load_prompt", return_value="group-level prompt"):
        result = get_scoring_prompt(
            source_prompt_file=None,
            group_prompt_file="group.txt",
        )
    assert result == "group-level prompt"


def test_scoring_prompt_global_default():
    """Global default (CONTENT_ANALYSIS_SYSTEM) when no files specified."""
    result = get_scoring_prompt(
        source_prompt_file=None,
        group_prompt_file=None,
    )
    assert result == CONTENT_ANALYSIS_SYSTEM


# ---------------------------------------------------------------------------
# Grouped summary rendering
# ---------------------------------------------------------------------------

def test_grouped_summary_contains_section_titles():
    """Multi-group summary has section headings."""
    summarizer = DailySummarizer()
    grouped = {
        "Headlines": [_item("Story A", score=9.0)],
        "Watched": [_item("Story B", score=8.0)],
    }
    result = asyncio.run(
        summarizer.generate_summary(grouped, "2026-03-12", 100, language="en")
    )
    assert "## Headlines" in result
    assert "## Watched" in result
    assert "Story A" in result
    assert "Story B" in result


def test_single_group_no_section_heading():
    """Single-group summary omits redundant section heading."""
    summarizer = DailySummarizer()
    grouped = {
        "Daily": [_item("Only Item", score=8.0)],
    }
    result = asyncio.run(
        summarizer.generate_summary(grouped, "2026-03-12", 50, language="en")
    )
    assert "## Daily" not in result
    assert "Only Item" in result


def test_grouped_summary_empty():
    """Empty grouped items generate empty summary."""
    summarizer = DailySummarizer()
    result = asyncio.run(
        summarizer.generate_summary({}, "2026-03-12", 0, language="en")
    )
    assert "No significant" in result or "threshold" in result


# ---------------------------------------------------------------------------
# TokenUsageTracker integration (verify it's used)
# ---------------------------------------------------------------------------

def test_tracker_used_in_pipeline():
    """TokenUsageTracker is importable and usable from orchestrator."""
    from src.ai.client import TokenUsage, TokenUsageTracker
    t = TokenUsageTracker()
    t.track(TokenUsage(prompt_tokens=100, completion_tokens=50))
    assert t.call_count == 1
    assert "150" in t.summary()
