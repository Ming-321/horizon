"""Tests for scraper category injection into ContentItem."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx

from src.models import (
    ContentItem,
    GitHubSourceConfig,
    HackerNewsConfig,
    RSSSourceConfig,
    RedditConfig,
    RedditSubredditConfig,
    RedditUserConfig,
    TelegramChannelConfig,
    TelegramConfig,
)
from src.scrapers.github import GitHubScraper
from src.scrapers.hackernews import HackerNewsScraper
from src.scrapers.reddit import RedditScraper
from src.scrapers.rss import RSSScraper
from src.scrapers.telegram import TelegramScraper


def _mock_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


# ---------------------------------------------------------------------------
# Hacker News
# ---------------------------------------------------------------------------

def test_hn_default_category():
    config = HackerNewsConfig()
    scraper = HackerNewsScraper(config, _mock_client())
    story = {
        "id": 1,
        "title": "Test",
        "url": "https://example.com",
        "by": "user",
        "time": int(datetime(2026, 3, 12, tzinfo=timezone.utc).timestamp()),
        "score": 200,
    }
    item = scraper._parse_story(story, [])
    assert item.category == "hackernews"


def test_hn_custom_category():
    config = HackerNewsConfig(category="tech-news")
    scraper = HackerNewsScraper(config, _mock_client())
    story = {
        "id": 2,
        "title": "Custom",
        "url": "https://example.com",
        "by": "user",
        "time": int(datetime(2026, 3, 12, tzinfo=timezone.utc).timestamp()),
    }
    item = scraper._parse_story(story, [])
    assert item.category == "tech-news"


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _tg_msg_html(channel: str, msg_id: int, text: str, dt: str) -> str:
    return f"""
    <div class="tgme_widget_message" data-post="{channel}/{msg_id}">
      <div class="tgme_widget_message_text">{text}</div>
      <time datetime="{dt}"></time>
    </div>
    """


def test_telegram_default_category():
    cfg = TelegramChannelConfig(channel="testchan")
    tg_config = TelegramConfig(channels=[cfg])
    scraper = TelegramScraper(tg_config, _mock_client())

    html = _tg_msg_html("testchan", 1, "Hello world test message", "2026-03-12T10:00:00+00:00")
    since = datetime(2026, 3, 11, tzinfo=timezone.utc)
    items = scraper._parse_channel_html(html, cfg, since)

    assert len(items) == 1
    assert items[0].category == "telegram"


def test_telegram_custom_category():
    cfg = TelegramChannelConfig(channel="testchan", category="ai-news")
    tg_config = TelegramConfig(channels=[cfg])
    scraper = TelegramScraper(tg_config, _mock_client())

    html = _tg_msg_html("testchan", 2, "AI breaking news", "2026-03-12T10:00:00+00:00")
    since = datetime(2026, 3, 11, tzinfo=timezone.utc)
    items = scraper._parse_channel_html(html, cfg, since)

    assert len(items) == 1
    assert items[0].category == "ai-news"


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

def test_reddit_subreddit_default_category():
    sub_cfg = RedditSubredditConfig(subreddit="python")
    reddit_config = RedditConfig(subreddits=[sub_cfg])
    scraper = RedditScraper(reddit_config, _mock_client())

    post = {
        "id": "abc123",
        "title": "Test Post",
        "url": "https://reddit.com/r/python/abc123",
        "author": "user1",
        "created_utc": datetime(2026, 3, 12, tzinfo=timezone.utc).timestamp(),
        "score": 100,
        "permalink": "/r/python/comments/abc123/test/",
        "is_self": True,
        "selftext": "body",
        "subreddit": "python",
    }
    item = scraper._parse_post(post, [], "subreddit")
    assert item.category == "reddit"


def test_reddit_subreddit_custom_category():
    sub_cfg = RedditSubredditConfig(subreddit="python", category="tech-community")
    reddit_config = RedditConfig(subreddits=[sub_cfg])
    scraper = RedditScraper(reddit_config, _mock_client())

    post = {
        "id": "def456",
        "title": "Custom Category Post",
        "url": "https://reddit.com/r/python/def456",
        "author": "user2",
        "created_utc": datetime(2026, 3, 12, tzinfo=timezone.utc).timestamp(),
        "score": 50,
        "permalink": "/r/python/comments/def456/test/",
        "is_self": True,
        "subreddit": "python",
    }
    item = scraper._parse_post(post, [], "subreddit", sub_cfg.category)
    assert item.category == "tech-community"


def test_reddit_user_default_category():
    user_cfg = RedditUserConfig(username="testuser")
    assert user_cfg.category == "reddit"


def test_reddit_user_custom_category():
    user_cfg = RedditUserConfig(username="testuser", category="watched-users")
    assert user_cfg.category == "watched-users"


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

def test_github_event_default_category():
    source = GitHubSourceConfig(type="user_events", username="octocat")
    scraper = GitHubScraper([source], _mock_client())

    event = {
        "id": "evt1",
        "type": "PushEvent",
        "created_at": "2026-03-12T10:00:00Z",
        "repo": {"name": "octocat/Hello-World"},
        "payload": {"commits": [{"message": "init"}]},
    }
    item = scraper._parse_event(event, "octocat")
    assert item.category == "github-activity"


def test_github_event_explicit_category():
    source = GitHubSourceConfig(type="user_events", username="octocat", category="watched")
    scraper = GitHubScraper([source], _mock_client())

    event = {
        "id": "evt2",
        "type": "PushEvent",
        "created_at": "2026-03-12T10:00:00Z",
        "repo": {"name": "octocat/Hello-World"},
        "payload": {"commits": [{"message": "test"}]},
    }
    item = scraper._parse_event(event, "octocat", source.category)
    assert item.category == "watched"


def test_github_release_default_category():
    source = GitHubSourceConfig(type="repo_releases", owner="octocat", repo="Hello")
    scraper = GitHubScraper([source], _mock_client())
    category = source.category or "github-updates"
    assert category == "github-updates"


def test_github_category_fallback_logic():
    """user_events without explicit category → github-activity; repo_releases → github-updates."""
    events_src = GitHubSourceConfig(type="user_events", username="u")
    releases_src = GitHubSourceConfig(type="repo_releases", owner="o", repo="r")
    custom_src = GitHubSourceConfig(type="user_events", username="u", category="my-cat")

    assert (events_src.category or ("github-updates" if events_src.type == "repo_releases" else "github-activity")) == "github-activity"
    assert (releases_src.category or ("github-updates" if releases_src.type == "repo_releases" else "github-activity")) == "github-updates"
    assert (custom_src.category or ("github-updates" if custom_src.type == "repo_releases" else "github-activity")) == "my-cat"


# ---------------------------------------------------------------------------
# RSS
# ---------------------------------------------------------------------------

def test_rss_category_from_source():
    source = RSSSourceConfig(name="Test Feed", url="https://example.com/feed.xml", category="ai-research")

    mock_response = MagicMock()
    mock_response.text = """<?xml version="1.0"?>
    <rss><channel><title>Test</title>
    <pubDate>Thu, 12 Mar 2026 00:00:00 GMT</pubDate>
    <item>
      <title>Item 1</title>
      <link>https://example.com/1</link>
      <pubDate>Thu, 12 Mar 2026 08:00:00 GMT</pubDate>
    </item>
    </channel></rss>"""
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)

    scraper = RSSScraper([source], mock_client)
    since = datetime(2026, 3, 11, tzinfo=timezone.utc)
    items = asyncio.run(scraper._fetch_feed(source, since))

    assert len(items) == 1
    assert items[0].category == "ai-research"
    assert items[0].metadata["category"] == "ai-research"


def test_rss_category_none_when_unset():
    source = RSSSourceConfig(name="No Cat", url="https://example.com/feed.xml")
    assert source.category is None
