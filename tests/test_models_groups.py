"""Tests for Phase 0: GroupConfig, category field, and config loading."""

import json
from pathlib import Path

from src.models import (
    Config, ContentItem, GroupConfig, ScoringConfig, SummaryGroupConfig,
    SourceType, HackerNewsConfig, TelegramChannelConfig, RedditSubredditConfig,
    RedditUserConfig, GitHubSourceConfig, RSSSourceConfig,
)


def test_group_config_defaults():
    g = GroupConfig(id="test", name="Test")
    assert g.default is False
    assert g.categories == []
    assert g.scoring.enabled is True
    assert g.scoring.threshold == 7.0
    assert g.scoring.prompt_file is None
    assert g.summary.prompt_file is None
    assert g.enrichment_mode == "full"
    assert g.enrichment_prompt_file is None


def test_group_config_enrichment_custom():
    g = GroupConfig(
        id="watched",
        name="Watched",
        enrichment_mode="summary_only",
        enrichment_prompt_file="enrichment_watched_summary.txt",
    )
    assert g.enrichment_mode == "summary_only"
    assert g.enrichment_prompt_file == "enrichment_watched_summary.txt"


def test_group_config_custom():
    g = GroupConfig(
        id="watched",
        name="Watched",
        default=False,
        categories=["github-updates"],
        scoring=ScoringConfig(enabled=False),
    )
    assert g.scoring.enabled is False
    assert g.categories == ["github-updates"]


def test_content_item_category_default():
    item = ContentItem(
        id="test:1",
        source_type=SourceType.RSS,
        title="Test",
        url="https://example.com",
        published_at="2026-01-01T00:00:00Z",
    )
    assert item.category is None


def test_content_item_category_set():
    item = ContentItem(
        id="test:2",
        source_type=SourceType.HACKERNEWS,
        title="Test",
        url="https://example.com",
        published_at="2026-01-01T00:00:00Z",
        category="hackernews",
    )
    assert item.category == "hackernews"


def test_config_loads_with_groups():
    config_path = Path("data/config.json")
    raw = json.loads(config_path.read_text())
    config = Config(**raw)
    assert len(config.groups) == 3
    assert config.groups[0].id == "headlines"
    assert config.groups[0].default is True
    assert config.groups[0].enrichment_mode == "full"
    assert config.groups[0].enrichment_prompt_file is None
    assert config.groups[1].id == "watched"
    assert config.groups[1].scoring.enabled is False
    assert config.groups[1].enrichment_mode == "summary_only"
    assert config.groups[2].id == "github_trending"
    assert config.groups[2].enrichment_mode == "full"


def test_config_loads_without_groups():
    """Old configs without groups field should still load (backward compat)."""
    raw = {
        "ai": {
            "provider": "openai",
            "model": "gpt-4",
            "api_key_env": "TEST_KEY",
        },
        "sources": {},
        "filtering": {"ai_score_threshold": 7.0},
    }
    config = Config(**raw)
    assert config.groups == []


def test_config_example_loads():
    config_path = Path("data/config.example.json")
    raw = json.loads(config_path.read_text())
    config = Config(**raw)
    assert len(config.groups) >= 1


def test_source_entry_defaults():
    hn = HackerNewsConfig()
    assert hn.category == "hackernews"
    assert hn.scoring_prompt_file is None

    tg = TelegramChannelConfig(channel="test")
    assert tg.category == "telegram"

    reddit_sub = RedditSubredditConfig(subreddit="test")
    assert reddit_sub.category == "reddit"

    reddit_user = RedditUserConfig(username="test")
    assert reddit_user.category == "reddit"

    gh = GitHubSourceConfig(type="repo_releases")
    assert gh.category is None
    assert gh.scoring_prompt_file is None

    rss = RSSSourceConfig(name="test", url="https://example.com/feed.xml")
    assert rss.category is None
    assert rss.scoring_prompt_file is None
