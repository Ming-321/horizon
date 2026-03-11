"""Tests for RSS date fallback, UA header, and Reddit config migration."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import feedparser

from src.scrapers.rss import RSSScraper
from src.models import RSSSourceConfig


def _make_source(name: str = "test-feed", url: str = "https://example.com/feed.xml") -> RSSSourceConfig:
    return RSSSourceConfig(name=name, url=url, enabled=True, category="test")


def _make_feed_xml(
    *,
    channel_pub_date: str | None = None,
    channel_updated: str | None = None,
    entries: list[dict] | None = None,
) -> str:
    """Build a minimal RSS 2.0 XML string."""
    parts = ['<?xml version="1.0"?>', "<rss><channel>", "<title>Test</title>"]
    if channel_pub_date:
        parts.append(f"<pubDate>{channel_pub_date}</pubDate>")
    if channel_updated:
        parts.append(f"<lastBuildDate>{channel_updated}</lastBuildDate>")
    for entry in (entries or []):
        parts.append("<item>")
        parts.append(f"<title>{entry.get('title', 'Untitled')}</title>")
        parts.append(f"<link>{entry.get('link', 'https://example.com')}</link>")
        if "pubDate" in entry:
            parts.append(f"<pubDate>{entry['pubDate']}</pubDate>")
        parts.append("</item>")
    parts.extend(["</channel>", "</rss>"])
    return "\n".join(parts)


def _make_mock_client(xml: str) -> AsyncMock:
    mock_response = MagicMock()
    mock_response.text = xml
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


# --- Fix 2: Date fallback tests ---

def test_entry_with_own_date_uses_it():
    """Entry with its own pubDate should use that date, not channel date."""
    source = _make_source()
    xml = _make_feed_xml(
        channel_pub_date="Sun, 09 Mar 2026 00:00:00 GMT",
        entries=[{"title": "Has Date", "link": "https://example.com/1",
                  "pubDate": "Mon, 10 Mar 2026 12:00:00 GMT"}],
    )
    scraper = RSSScraper([source], _make_mock_client(xml))
    since = datetime(2026, 3, 9, tzinfo=timezone.utc)
    items = asyncio.run(scraper._fetch_feed(source, since))

    assert len(items) == 1
    assert items[0].published_at.day == 10


def test_entry_without_date_falls_back_to_channel():
    """Entry without date should use channel-level pubDate."""
    source = _make_source()
    xml = _make_feed_xml(
        channel_pub_date="Mon, 10 Mar 2026 08:00:00 GMT",
        entries=[{"title": "No Date", "link": "https://example.com/2"}],
    )
    scraper = RSSScraper([source], _make_mock_client(xml))
    since = datetime(2026, 3, 9, tzinfo=timezone.utc)
    items = asyncio.run(scraper._fetch_feed(source, since))

    assert len(items) == 1
    assert items[0].published_at.day == 10
    assert items[0].published_at.hour == 8


def test_entry_without_date_no_channel_date_falls_back_to_now():
    """Entry without date and no channel date falls back to now().

    This is intentional for feeds like GitHub Trending that lack any date
    metadata — items in such feeds are implicitly "today's" content.
    """
    source = _make_source()
    xml = _make_feed_xml(
        channel_pub_date=None,
        entries=[{"title": "No Date At All", "link": "https://example.com/3"}],
    )
    scraper = RSSScraper([source], _make_mock_client(xml))
    since = datetime(2026, 3, 1, tzinfo=timezone.utc)

    before = datetime.now(timezone.utc)
    items = asyncio.run(scraper._fetch_feed(source, since))
    after = datetime.now(timezone.utc)

    assert len(items) == 1
    assert before <= items[0].published_at <= after


def test_old_channel_date_filters_out_entries():
    """If channel date is older than since, entry should be filtered out."""
    source = _make_source()
    xml = _make_feed_xml(
        channel_pub_date="Mon, 01 Jan 2024 00:00:00 GMT",
        entries=[{"title": "Old Feed", "link": "https://example.com/4"}],
    )
    scraper = RSSScraper([source], _make_mock_client(xml))
    since = datetime(2026, 3, 10, tzinfo=timezone.utc)
    items = asyncio.run(scraper._fetch_feed(source, since))

    assert len(items) == 0


def test_updated_parsed_branch_as_fallback():
    """Channel with lastBuildDate (mapped to updated_parsed) should work as fallback."""
    source = _make_source()
    xml = _make_feed_xml(
        channel_updated="Tue, 11 Mar 2026 06:00:00 GMT",
        entries=[{"title": "Updated Only", "link": "https://example.com/5"}],
    )
    scraper = RSSScraper([source], _make_mock_client(xml))
    since = datetime(2026, 3, 10, tzinfo=timezone.utc)
    items = asyncio.run(scraper._fetch_feed(source, since))

    assert len(items) == 1
    assert items[0].published_at.day == 11


def test_corrupted_channel_date_does_not_crash():
    """If channel date metadata is corrupted, scraper should still process entries."""
    source = _make_source()
    xml = _make_feed_xml(
        entries=[{"title": "Good Entry", "link": "https://example.com/6",
                  "pubDate": "Tue, 11 Mar 2026 10:00:00 GMT"}],
    )
    fake_feed = feedparser.parse(xml)
    fake_feed.feed["published_parsed"] = "not-a-time-struct"

    mock_client = _make_mock_client(xml)
    scraper = RSSScraper([source], mock_client)
    since = datetime(2026, 3, 10, tzinfo=timezone.utc)

    with patch("src.scrapers.rss.feedparser.parse", return_value=fake_feed):
        items = asyncio.run(scraper._fetch_feed(source, since))

    assert len(items) == 1
    assert items[0].title == "Good Entry"


# --- Fix 1: UA header test ---

def test_orchestrator_sets_browser_ua():
    """fetch_all_sources should create httpx.AsyncClient with browser User-Agent."""
    import src.orchestrator as orch_module

    source_text = Path(__file__).resolve().parents[1] / "src" / "orchestrator.py"
    source_code = source_text.read_text()
    assert "User-Agent" in source_code
    assert "Horizon/1.0" in source_code

    ua_line = [l for l in source_code.splitlines() if "User-Agent" in l][0]
    assert "Mozilla/5.0" in ua_line


# --- Fix 3: Reddit config migration test ---

def test_reddit_disabled_and_rss_sources_present():
    """Config should have Reddit disabled and Reddit subreddits as RSS sources."""
    config_path = Path(__file__).resolve().parents[1] / "data" / "config.json"
    config = json.loads(config_path.read_text())

    assert config["sources"]["reddit"]["enabled"] is False

    rss_names = [s["name"] for s in config["sources"]["rss"]]
    assert "r/MachineLearning" in rss_names
    assert "r/LocalLLaMA" in rss_names

    reddit_rss = [s for s in config["sources"]["rss"] if s["name"].startswith("r/")]
    for source in reddit_rss:
        assert "old.reddit.com" in source["url"]
        assert source["enabled"] is True
