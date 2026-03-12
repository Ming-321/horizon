"""Tests for enrichment mode routing and summarize_batch behavior."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.models import ContentItem, GroupConfig, SourceType
from src.ai.enricher import ContentEnricher, _DEFAULT_SUMMARY_PROMPT
from src.ai.client import TokenUsage
from src.ai.prompts import load_enrichment_prompt


def _make_item(item_id: str = "test:1", category: str = "hackernews") -> ContentItem:
    return ContentItem(
        id=item_id,
        source_type=SourceType.RSS,
        title="Test Item",
        url="https://example.com/1",
        content="Some test content here for enrichment.",
        published_at=datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc),
        category=category,
    )


def test_load_enrichment_prompt_configured():
    group = GroupConfig(
        id="watched", name="Watched",
        enrichment_prompt_file="enrichment_watched_summary.txt",
    )
    prompt = load_enrichment_prompt(group)
    assert prompt is not None
    assert len(prompt) > 0


def test_load_enrichment_prompt_null():
    group = GroupConfig(id="headlines", name="Headlines", enrichment_prompt_file=None)
    prompt = load_enrichment_prompt(group)
    assert prompt is None


def test_load_enrichment_prompt_missing_file():
    group = GroupConfig(
        id="test", name="Test",
        enrichment_prompt_file="nonexistent_file.txt",
    )
    prompt = load_enrichment_prompt(group)
    assert prompt is None


def test_summarize_batch_writes_metadata():
    mock_client = AsyncMock()
    mock_client.complete.return_value = MagicMock(
        text='{"summary_en": "English summary", "summary_zh": "中文摘要"}',
        usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
    )

    enricher = ContentEnricher(mock_client)
    item = _make_item()

    usage = asyncio.run(enricher.summarize_batch([item]))

    assert item.metadata["detailed_summary_en"] == "English summary"
    assert item.metadata["detailed_summary_zh"] == "中文摘要"
    assert item.metadata["detailed_summary"] == "English summary"
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50


def test_summarize_batch_uses_custom_prompt():
    mock_client = AsyncMock()
    mock_client.complete.return_value = MagicMock(
        text='{"summary_en": "Custom", "summary_zh": "自定义"}',
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
    )

    enricher = ContentEnricher(mock_client)
    item = _make_item()

    asyncio.run(enricher.summarize_batch([item], system_prompt="Custom prompt"))

    call_kwargs = mock_client.complete.call_args
    assert call_kwargs.kwargs.get("system") == "Custom prompt" or call_kwargs[1].get("system") == "Custom prompt"


def test_summarize_batch_uses_default_prompt_when_none():
    mock_client = AsyncMock()
    mock_client.complete.return_value = MagicMock(
        text='{"summary_en": "Default", "summary_zh": "默认"}',
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
    )

    enricher = ContentEnricher(mock_client)
    item = _make_item()

    asyncio.run(enricher.summarize_batch([item], system_prompt=None))

    call_kwargs = mock_client.complete.call_args
    system_used = call_kwargs.kwargs.get("system") or call_kwargs[1].get("system", "")
    assert system_used == _DEFAULT_SUMMARY_PROMPT


def test_summarize_does_not_call_web_search():
    mock_client = AsyncMock()
    mock_client.complete.return_value = MagicMock(
        text='{"summary_en": "Test", "summary_zh": "测试"}',
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
    )

    enricher = ContentEnricher(mock_client)
    item = _make_item()

    with patch.object(enricher, "_web_search", new_callable=AsyncMock) as mock_search, \
         patch.object(enricher, "_extract_concepts", new_callable=AsyncMock) as mock_concepts:
        asyncio.run(enricher.summarize_batch([item]))
        mock_search.assert_not_called()
        mock_concepts.assert_not_called()


def test_enrich_batch_accepts_custom_prompt():
    mock_client = AsyncMock()
    mock_client.complete.return_value = MagicMock(
        text='{"whats_new_en": "test", "whats_new_zh": "测试"}',
        usage=TokenUsage(prompt_tokens=50, completion_tokens=25),
    )

    enricher = ContentEnricher(mock_client)
    item = _make_item()
    item.ai_score = 8
    item.ai_summary = "Test"
    item.ai_tags = ["test"]

    with patch.object(enricher, "_extract_concepts", new_callable=AsyncMock,
                      return_value=([], TokenUsage())), \
         patch.object(enricher, "_web_search", new_callable=AsyncMock,
                      return_value=[]):
        asyncio.run(enricher.enrich_batch(
            [item], enrichment_system_prompt="Custom enrich prompt",
        ))

    complete_calls = mock_client.complete.call_args_list
    enrich_call = complete_calls[-1]
    system_used = enrich_call.kwargs.get("system") or enrich_call[1].get("system", "")
    assert system_used == "Custom enrich prompt"


def test_enrich_batch_falls_back_to_default():
    from src.ai.prompts import CONTENT_ENRICHMENT_SYSTEM

    mock_client = AsyncMock()
    mock_client.complete.return_value = MagicMock(
        text='{"whats_new_en": "test", "whats_new_zh": "测试"}',
        usage=TokenUsage(prompt_tokens=50, completion_tokens=25),
    )

    enricher = ContentEnricher(mock_client)
    item = _make_item()
    item.ai_score = 8
    item.ai_summary = "Test"
    item.ai_tags = ["test"]

    with patch.object(enricher, "_extract_concepts", new_callable=AsyncMock,
                      return_value=([], TokenUsage())), \
         patch.object(enricher, "_web_search", new_callable=AsyncMock,
                      return_value=[]):
        asyncio.run(enricher.enrich_batch([item], enrichment_system_prompt=None))

    complete_calls = mock_client.complete.call_args_list
    enrich_call = complete_calls[-1]
    system_used = enrich_call.kwargs.get("system") or enrich_call[1].get("system", "")
    assert system_used == CONTENT_ENRICHMENT_SYSTEM
