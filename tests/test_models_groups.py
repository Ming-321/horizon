"""Tests for Phase 0: GroupConfig, category field, and config loading."""

import json
from pathlib import Path

from src.models import (
    Config, ContentItem, GroupConfig, ScoringConfig, SummaryGroupConfig,
    SourceType, HackerNewsConfig, TelegramChannelConfig, RedditSubredditConfig,
    RedditUserConfig, GitHubSourceConfig, RSSSourceConfig,
    BriefConfig, HtmlConfig, PodcastConfig, OutputConfig, WxPusherConfig,
    NotificationsConfig,
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


def test_brief_config_defaults():
    c = BriefConfig()
    assert c.enabled is False
    assert c.top_n == 10


def test_html_config_defaults():
    c = HtmlConfig()
    assert c.enabled is False
    assert c.serve_port == 8080


def test_podcast_config_defaults():
    c = PodcastConfig()
    assert c.enabled is False
    assert c.tts_model == "cosyvoice-v3-flash"
    assert c.tts_endpoint == "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
    assert c.voice_a == "longanyang"
    assert c.voice_b == "longanhuan"
    assert c.max_script_chars == 3000
    assert c.items_per_group == 3
    assert c.output_dir == "data/podcasts"
    assert c.prompt_file == "podcast_dialogue.txt"
    assert c.r2_bucket == ""
    assert c.r2_endpoint == ""
    assert c.r2_public_url == ""
    assert c.github_repo == ""
    assert c.github_pages_url == ""
    assert c.feed_title == "Horizon 科技日报"
    assert c.feed_description == "AI 生成的每日科技新闻播客"


def test_output_config_defaults():
    c = OutputConfig()
    assert c.brief.enabled is False
    assert c.html.enabled is False
    assert c.podcast.enabled is False


def test_wxpusher_config_defaults():
    c = WxPusherConfig()
    assert c.enabled is False
    assert c.app_token_env == "WXPUSHER_APP_TOKEN"
    assert c.uids_env == "WXPUSHER_UIDS"
    assert c.topic_ids_env == "WXPUSHER_TOPIC_IDS"


def test_notifications_config_defaults():
    c = NotificationsConfig()
    assert c.wxpusher.enabled is False


def test_config_output_and_notifications_defaults():
    raw = {
        "ai": {"provider": "openai", "model": "gpt-4", "api_key_env": "KEY"},
        "sources": {},
        "filtering": {"ai_score_threshold": 7.0},
    }
    config = Config(**raw)
    assert config.output.brief.enabled is False
    assert config.output.html.enabled is False
    assert config.output.podcast.enabled is False
    assert config.notifications.wxpusher.enabled is False


def test_config_loads_output_and_notifications():
    config_path = Path("data/config.json")
    raw = json.loads(config_path.read_text())
    config = Config(**raw)
    assert config.output.brief.enabled is True
    assert config.output.brief.top_n == 10
    assert config.output.html.enabled is True
    assert config.output.html.serve_port == 8080
    assert config.output.podcast.enabled is True
    assert config.output.podcast.tts_model == "cosyvoice-v3-flash"
    assert isinstance(config.notifications.wxpusher.enabled, bool)


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
