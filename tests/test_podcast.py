"""Tests for podcast generation pipeline."""

import asyncio
import json
import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai.client import CompletionResult, TokenUsage
from src.models import ContentItem, PodcastConfig, SourceType
from src.services.podcast import (
    AudioMerger,
    PodcastPipeline,
    ScriptGenerator,
    TTSSynthesizer,
)


def _make_items(n=5, group="headlines"):
    return [
        ContentItem(
            id=f"test:{group}:{i}",
            source_type=SourceType.RSS,
            title=f"News Item {i}",
            url=f"https://example.com/{i}",
            published_at="2026-03-12T00:00:00Z",
            ai_score=9.0 - i * 0.5,
            ai_summary=f"Summary of item {i}.",
            ai_tags=["ai", "tech", f"tag{i}"],
        )
        for i in range(n)
    ]


VALID_SCRIPT = [
    {"speaker": "A", "text": "大家好，欢迎收听今天的科技播客。" * 10},
    {"speaker": "B", "text": "嗨，今天有什么好消息吗？" * 10},
    {"speaker": "A", "text": "今天最大的新闻是关于人工智能的最新突破。" * 10},
    {"speaker": "B", "text": "哇，这确实很令人兴奋！让我们深入了解一下。" * 10},
    {"speaker": "A", "text": "好的，首先来看看这项新技术的细节。" * 10},
    {"speaker": "B", "text": "感谢收听，我们下次再见！" * 10},
]


# ------------------------------------------------------------------
# ScriptGenerator
# ------------------------------------------------------------------


def test_script_generator_parse():
    """LLM returns {"script": [...]}, generator parses correctly."""
    mock_client = AsyncMock()
    mock_client.complete.return_value = CompletionResult(
        text=json.dumps({"script": VALID_SCRIPT}),
        usage=TokenUsage(),
    )

    gen = ScriptGenerator(mock_client, "system prompt", 3000)
    result = asyncio.run(gen.generate(_make_items(3), "2026-03-12"))

    assert len(result) == len(VALID_SCRIPT)
    assert all(seg["speaker"] in ("A", "B") for seg in result)
    mock_client.complete.assert_called_once()


def test_script_generator_invalid_json():
    """LLM returns invalid JSON → empty list."""
    mock_client = AsyncMock()
    mock_client.complete.return_value = CompletionResult(
        text="not json at all", usage=TokenUsage(),
    )
    gen = ScriptGenerator(mock_client, "prompt", 3000)
    result = asyncio.run(gen.generate(_make_items(3), "2026-03-12"))
    assert result == []


def test_script_validation_rejects_bad_speaker():
    """Speaker not A/B → validation fails."""
    bad_script = [
        {"speaker": "C", "text": "hello " * 200},
        {"speaker": "A", "text": "hello " * 200},
        {"speaker": "B", "text": "world " * 200},
        {"speaker": "A", "text": "end " * 200},
    ]
    assert ScriptGenerator._validate_script(bad_script) is False


def test_script_validation_rejects_empty_text():
    """Empty text → validation fails."""
    bad_script = [
        {"speaker": "A", "text": ""},
        {"speaker": "B", "text": "hello " * 200},
        {"speaker": "A", "text": "world " * 200},
        {"speaker": "B", "text": "end " * 200},
    ]
    assert ScriptGenerator._validate_script(bad_script) is False


def test_script_validation_rejects_too_few_segments():
    """Fewer than 4 segments → validation fails."""
    short_script = [
        {"speaker": "A", "text": "hello " * 200},
        {"speaker": "B", "text": "world " * 200},
    ]
    assert ScriptGenerator._validate_script(short_script) is False


def test_script_validation_accepts_valid():
    """Valid script passes validation."""
    assert ScriptGenerator._validate_script(VALID_SCRIPT) is True


# ------------------------------------------------------------------
# TTSSynthesizer
# ------------------------------------------------------------------


def test_tts_synthesizer_voice_mapping():
    """Speaker A/B maps to correct voice IDs."""
    tts = TTSSynthesizer(
        model="cosyvoice-v3-flash",
        endpoint="wss://example.com",
        voice_map={"A": "longanyang", "B": "longanhuan"},
    )
    assert tts.voice_map["A"] == "longanyang"
    assert tts.voice_map["B"] == "longanhuan"


@patch("src.services.podcast.TTSSynthesizer._get_api_key", return_value="fake-key")
@patch("src.services.podcast.TTSSynthesizer._synthesize_one")
def test_tts_synthesizer_produces_files(mock_synth_one, mock_key, tmp_path):
    """Each successful segment produces an MP3 file."""
    mock_synth_one.return_value = b"\xff\xfb\x90\x00" * 100  # fake mp3 bytes

    tts = TTSSynthesizer(
        model="cosyvoice-v3-flash",
        endpoint="wss://example.com",
        voice_map={"A": "longanyang", "B": "longanhuan"},
    )
    script = [
        {"speaker": "A", "text": "Hello"},
        {"speaker": "B", "text": "World"},
    ]
    segments = asyncio.run(tts.synthesize(script, tmp_path))
    assert len(segments) == 2
    assert all(p.exists() and p.stat().st_size > 0 for p in segments)


@patch("src.services.podcast.TTSSynthesizer._get_api_key", return_value="fake-key")
@patch("src.services.podcast.TTSSynthesizer._synthesize_one")
def test_tts_synthesizer_skips_failed_segment(mock_synth_one, mock_key, tmp_path):
    """Failed segment is skipped, rest continue."""
    mock_synth_one.side_effect = [b"\xff" * 100, Exception("TTS error"), b"\xff" * 100]

    tts = TTSSynthesizer(
        model="cosyvoice-v3-flash",
        endpoint="wss://example.com",
        voice_map={"A": "longanyang", "B": "longanhuan"},
    )
    script = [
        {"speaker": "A", "text": "seg1"},
        {"speaker": "B", "text": "seg2"},
        {"speaker": "A", "text": "seg3"},
    ]
    segments = asyncio.run(tts.synthesize(script, tmp_path))
    assert len(segments) == 2


# ------------------------------------------------------------------
# AudioMerger
# ------------------------------------------------------------------


def test_audio_merger_ffmpeg_command(tmp_path):
    """Verify merge generates correct concat list and calls ffmpeg."""
    seg1 = tmp_path / "seg_0000.mp3"
    seg2 = tmp_path / "seg_0001.mp3"
    seg1.write_bytes(b"\xff\xfb\x90\x00" * 50)
    seg2.write_bytes(b"\xff\xfb\x90\x00" * 50)

    output = tmp_path / "out" / "output.mp3"

    async def _run():
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate.return_value = (b"", b"")
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await AudioMerger.merge([seg1, seg2], output)

            args = mock_exec.call_args[0]
            assert "ffmpeg" in args[0] or args[0] == "ffmpeg"
            assert "-f" in args
            assert "concat" in args
            assert "-c" in args
            assert "copy" in args

            concat_file = tmp_path / "concat.txt"
            assert concat_file.exists()
            content = concat_file.read_text()
            assert str(seg1.resolve()) in content
            assert str(seg2.resolve()) in content

            return result

    result = asyncio.run(_run())
    assert result == output


def test_audio_merger_empty_segments():
    """Empty segments → returns None without calling ffmpeg."""
    result = asyncio.run(AudioMerger.merge([], Path("/tmp/out.mp3")))
    assert result is None


def test_audio_merger_no_ffmpeg(tmp_path):
    """Missing ffmpeg → raises FileNotFoundError."""
    seg = tmp_path / "seg.mp3"
    seg.write_bytes(b"\xff" * 10)
    with patch("shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="ffmpeg"):
            asyncio.run(AudioMerger.merge([seg], tmp_path / "out.mp3"))


# ------------------------------------------------------------------
# PodcastPipeline
# ------------------------------------------------------------------


def test_pipeline_no_items_returns_none():
    """Empty input → returns None."""
    mock_client = AsyncMock()
    config = PodcastConfig(enabled=True)

    with patch("src.services.podcast.load_prompt", return_value="prompt text"):
        pipeline = PodcastPipeline(config, mock_client)

    result = asyncio.run(pipeline.generate({}, "2026-03-12"))
    assert result is None


def test_pipeline_exception_returns_none():
    """Exception in sub-component → returns None (not raised)."""
    mock_client = AsyncMock()
    mock_client.complete.side_effect = RuntimeError("LLM down")
    config = PodcastConfig(enabled=True)

    with patch("src.services.podcast.load_prompt", return_value="prompt text"):
        pipeline = PodcastPipeline(config, mock_client)

    grouped = {"头条速递": _make_items(3)}
    result = asyncio.run(pipeline.generate(grouped, "2026-03-12"))
    assert result is None


def test_select_items_balanced():
    """Selection draws from multiple sub-topics, no single-topic monopoly."""
    mock_client = AsyncMock()

    topics = [
        {"name": "AI", "item_indices": [0, 1, 2, 3, 4]},
        {"name": "Security", "item_indices": [5, 6, 7, 8, 9]},
    ]
    mock_client.complete.return_value = CompletionResult(
        text=json.dumps({"topics": topics}), usage=TokenUsage(),
    )

    config = PodcastConfig(enabled=True, items_per_group=2)

    with patch("src.services.podcast.load_prompt", return_value="prompt text"):
        pipeline = PodcastPipeline(config, mock_client)

    items = _make_items(10)
    selected = asyncio.run(pipeline._select_items({"头条速递": items}))

    ai_items = set(range(5))
    sec_items = set(range(5, 10))
    selected_ids = {int(it.id.split(":")[-1]) for it in selected}

    assert selected_ids & ai_items, "Should include items from AI topic"
    assert selected_ids & sec_items, "Should include items from Security topic"
    assert len(selected) >= 4, "Should select from both topics"


def test_select_items_no_scores_takes_all():
    """Items without scores (e.g. watched group) are all included."""
    mock_client = AsyncMock()
    config = PodcastConfig(enabled=True)

    with patch("src.services.podcast.load_prompt", return_value="prompt text"):
        pipeline = PodcastPipeline(config, mock_client)

    items = [
        ContentItem(
            id=f"test:watched:{i}",
            source_type=SourceType.RSS,
            title=f"Update {i}",
            url=f"https://example.com/{i}",
            published_at="2026-03-12T00:00:00Z",
        )
        for i in range(3)
    ]
    selected = asyncio.run(pipeline._select_items({"关注动态": items}))
    assert len(selected) == 3


def test_pipeline_prompt_file_missing():
    """Missing prompt file raises ValueError at construction."""
    mock_client = AsyncMock()
    config = PodcastConfig(enabled=True, prompt_file="nonexistent.txt")

    with patch("src.services.podcast.load_prompt", return_value=None):
        with pytest.raises(ValueError, match="Podcast prompt file not found"):
            PodcastPipeline(config, mock_client)
