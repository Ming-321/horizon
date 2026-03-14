"""Tests for podcast generation pipeline."""

import asyncio
import json
import math
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.ai.client import CompletionResult, TokenUsage
from src.models import ContentItem, PodcastConfig, SourceType
from src.services.podcast import (
    AudioMerger,
    FeedGenerator,
    GitHubUploader,
    PodcastPipeline,
    R2Uploader,
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


def test_script_generator_retries_after_invalid_output():
    """Generator retries when the first completion is malformed."""
    mock_client = AsyncMock()
    mock_client.complete.side_effect = [
        CompletionResult(text="not json", usage=TokenUsage()),
        CompletionResult(text=json.dumps({"script": VALID_SCRIPT}), usage=TokenUsage()),
    ]

    gen = ScriptGenerator(mock_client, "system prompt", 3000)
    result = asyncio.run(gen.generate(_make_items(3), "2026-03-12"))

    assert result == VALID_SCRIPT
    assert mock_client.complete.await_count == 2
    assert gen.last_error is None


def test_script_generator_repairs_minor_format_issues():
    """Minor schema and speaker formatting drift is normalized automatically."""
    repaired_source = [
        {"speaker": "主持人A", "content": "大家好，欢迎收听今天的科技播客。" * 18},
        {"role": "主持人B", "message": "今天的新闻非常密集，我们一条一条展开。" * 15},
        "A：" + "第一条新闻涉及模型、工具链和开发效率的变化。" * 15,
        {"name": "B", "dialogue": "这类变化会直接影响开发者的工作流和部署方式。" * 14},
    ]

    mock_client = AsyncMock()
    mock_client.complete.return_value = CompletionResult(
        text=json.dumps({"script": repaired_source}, ensure_ascii=False),
        usage=TokenUsage(),
    )

    gen = ScriptGenerator(mock_client, "system prompt", 3000)
    result = asyncio.run(gen.generate(_make_items(3), "2026-03-12"))

    assert len(result) == 4
    assert [seg["speaker"] for seg in result] == ["A", "B", "A", "B"]
    assert all(seg["text"] for seg in result)
    assert ScriptGenerator._validate_script(result) is True


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


def test_pipeline_records_script_failure_reason():
    """Pipeline exposes the script failure reason for alerting."""
    mock_client = AsyncMock()
    mock_client.complete.return_value = CompletionResult(
        text=json.dumps({"script": [{"speaker": "主持人A", "content": ""}]}),
        usage=TokenUsage(),
    )
    config = PodcastConfig(enabled=True)

    with patch("src.services.podcast.load_prompt", return_value="prompt text"):
        pipeline = PodcastPipeline(config, mock_client)

    grouped = {"头条速递": _make_items(3)}
    result = asyncio.run(pipeline.generate(grouped, "2026-03-12"))

    assert result is None
    assert pipeline.last_failure_reason is not None
    assert "script" in pipeline.last_failure_reason


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


# ------------------------------------------------------------------
# GitHubUploader
# ------------------------------------------------------------------


def test_github_uploader_create_release_and_upload(tmp_path):
    """Upload creates release and uploads asset, returns download URL."""
    test_file = tmp_path / "horizon-2026-03-13.mp3"
    test_file.write_bytes(b"\xff" * 100)

    async def _run():
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            uploader = GitHubUploader(
                repo="user/repo",
                pages_url="https://user.github.io/repo",
            )

        mock_create_resp = MagicMock()
        mock_create_resp.status_code = 201
        mock_create_resp.json.return_value = {
            "upload_url": "https://uploads.github.com/repos/user/repo/releases/1/assets{?name}",
        }

        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 201
        mock_upload_resp.json.return_value = {
            "browser_download_url": "https://github.com/user/repo/releases/download/podcast-2026-03-13/horizon-2026-03-13.mp3",
        }

        mock_client = AsyncMock()
        mock_client.post.side_effect = [mock_create_resp, mock_upload_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            url = await uploader.create_release_and_upload(test_file, "podcast-2026-03-13")

        assert "horizon-2026-03-13.mp3" in url
        assert mock_client.post.call_count == 2

    asyncio.run(_run())


def test_github_uploader_reuses_existing_release(tmp_path):
    """If release already exists (422), fetches and reuses it."""
    test_file = tmp_path / "test.mp3"
    test_file.write_bytes(b"\xff" * 50)

    async def _run():
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            uploader = GitHubUploader(repo="user/repo", pages_url="https://u.github.io/r")

        mock_422_resp = MagicMock()
        mock_422_resp.status_code = 422

        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 200
        mock_get_resp.json.return_value = {
            "upload_url": "https://uploads.github.com/releases/1/assets{?name}",
        }

        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 201
        mock_upload_resp.json.return_value = {
            "browser_download_url": "https://github.com/u/r/releases/download/tag/test.mp3",
        }

        mock_client = AsyncMock()
        mock_client.post.side_effect = [mock_422_resp, mock_upload_resp]
        mock_client.get.return_value = mock_get_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            url = await uploader.create_release_and_upload(test_file, "tag")

        assert "test.mp3" in url
        mock_client.get.assert_called_once()

    asyncio.run(_run())


def test_github_uploader_missing_token():
    """Missing GITHUB_TOKEN raises EnvironmentError."""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("GITHUB_TOKEN", None)
        with pytest.raises(EnvironmentError, match="GITHUB_TOKEN"):
            GitHubUploader(repo="user/repo", pages_url="https://u.github.io/r")


def test_github_uploader_feed_path():
    """Feed path is docs/podcast/feed.xml."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake"}):
        uploader = GitHubUploader(repo="user/repo", pages_url="https://u.github.io/repo")
    assert uploader.get_feed_path() == Path("docs/podcast/feed.xml")
    assert uploader.get_feed_url() == "https://u.github.io/repo/podcast/feed.xml"


# ------------------------------------------------------------------
# R2Uploader
# ------------------------------------------------------------------


def test_r2_uploader_upload(tmp_path):
    """Upload returns public URL."""
    test_file = tmp_path / "test.mp3"
    test_file.write_bytes(b"\xff" * 100)

    uploader = R2Uploader(
        bucket="test-bucket",
        endpoint="https://fake.r2.dev",
        public_url="https://pub-abc.r2.dev",
    )

    async def _run():
        with patch.object(uploader, "_get_client") as mock_get:
            mock_client = MagicMock()
            mock_get.return_value = mock_client
            url = await uploader.upload(test_file, "episodes/test.mp3", "audio/mpeg")
            mock_client.upload_file.assert_called_once_with(
                str(test_file), "test-bucket", "episodes/test.mp3",
                ExtraArgs={"ContentType": "audio/mpeg"},
            )
            return url

    url = asyncio.run(_run())
    assert url == "https://pub-abc.r2.dev/episodes/test.mp3"


def test_r2_uploader_download_text():
    """Download text returns content or None on failure."""
    uploader = R2Uploader(
        bucket="test-bucket",
        endpoint="https://fake.r2.dev",
        public_url="https://pub-abc.r2.dev",
    )

    async def _run():
        with patch.object(uploader, "_get_client") as mock_get:
            mock_client = MagicMock()
            mock_resp = {"Body": MagicMock()}
            mock_resp["Body"].read.return_value = b"<rss>existing</rss>"
            mock_client.get_object.return_value = mock_resp
            mock_get.return_value = mock_client
            result = await uploader.download_text("feed.xml")
            assert result == "<rss>existing</rss>"

    asyncio.run(_run())


def test_r2_uploader_download_text_not_found():
    """Download returns None when key doesn't exist."""
    uploader = R2Uploader(
        bucket="test-bucket",
        endpoint="https://fake.r2.dev",
        public_url="https://pub-abc.r2.dev",
    )

    async def _run():
        with patch.object(uploader, "_get_client") as mock_get:
            mock_client = MagicMock()
            mock_client.get_object.side_effect = Exception("NoSuchKey")
            mock_get.return_value = mock_client
            result = await uploader.download_text("feed.xml")
            assert result is None

    asyncio.run(_run())


# ------------------------------------------------------------------
# FeedGenerator
# ------------------------------------------------------------------


def test_feed_generator_new_feed():
    """Generate a new feed from scratch."""
    gen = FeedGenerator(
        title="Test Podcast",
        description="Test Description",
        public_url="https://pub-abc.r2.dev",
    )
    xml = gen.update_feed("2026-03-13", "https://pub-abc.r2.dev/ep.mp3", 7000000)

    assert '<?xml version="1.0"' in xml
    assert "<title>Test Podcast</title>" in xml
    assert "<description>Test Description</description>" in xml
    assert "<language>zh-cn</language>" in xml
    assert 'url="https://pub-abc.r2.dev/ep.mp3"' in xml
    assert 'length="7000000"' in xml
    assert 'type="audio/mpeg"' in xml
    assert "<guid>https://pub-abc.r2.dev/ep.mp3</guid>" in xml
    assert "<pubDate>" in xml


def test_feed_generator_update_existing():
    """Update an existing feed preserves old episodes."""
    gen = FeedGenerator(
        title="Test Podcast",
        description="Test",
        public_url="https://pub-abc.r2.dev",
    )
    feed1 = gen.update_feed("2026-03-12", "https://r2.dev/ep1.mp3", 5000000)
    feed2 = gen.update_feed("2026-03-13", "https://r2.dev/ep2.mp3", 6000000, feed1)

    assert "ep1.mp3" in feed2
    assert "ep2.mp3" in feed2
    items = feed2.count("<item>")
    assert items == 2


def test_feed_generator_invalid_existing_xml():
    """Invalid existing XML creates a new feed instead of crashing."""
    gen = FeedGenerator(
        title="Test",
        description="Test",
        public_url="https://pub-abc.r2.dev",
    )
    xml = gen.update_feed("2026-03-13", "https://r2.dev/ep.mp3", 1000, "not xml!!!")
    assert "<title>Test</title>" in xml
    assert "ep.mp3" in xml


# ------------------------------------------------------------------
# Pipeline + R2 integration
# ------------------------------------------------------------------


def test_pipeline_uploads_when_r2_configured(tmp_path):
    """Pipeline calls R2 upload + feed update when r2_bucket is set."""
    mock_client = AsyncMock()
    config = PodcastConfig(
        enabled=True,
        r2_bucket="test-bucket",
        r2_endpoint="https://fake.r2.dev",
        r2_public_url="https://pub-abc.r2.dev",
    )

    fake_mp3 = tmp_path / "horizon-2026-03-13.mp3"
    fake_mp3.write_bytes(b"\xff\xfb\x90\x00" * 100)

    async def _run():
        with patch("src.services.podcast.load_prompt", return_value="prompt"), \
             patch.object(PodcastPipeline, "_select_items", return_value=_make_items(3)), \
             patch.object(PodcastPipeline, "_upload_and_update_feed") as mock_upload:
            mock_upload.return_value = None

            mock_client.complete.return_value = CompletionResult(
                text=json.dumps({"script": VALID_SCRIPT}),
                usage=TokenUsage(),
            )

            with patch("src.services.podcast.TTSSynthesizer.synthesize") as mock_tts, \
                 patch("src.services.podcast.AudioMerger.merge") as mock_merge:
                mock_tts.return_value = [tmp_path / "seg.mp3"]
                mock_merge.return_value = fake_mp3

                pipeline = PodcastPipeline(config, mock_client)
                result = await pipeline.generate({"headlines": _make_items(3)}, "2026-03-13")

                assert result == fake_mp3
                mock_upload.assert_called_once_with(fake_mp3, "2026-03-13")

    asyncio.run(_run())


def test_pipeline_skips_upload_when_no_r2():
    """Pipeline skips R2 upload when r2_bucket is empty."""
    mock_client = AsyncMock()
    config = PodcastConfig(enabled=True, r2_bucket="")

    async def _run():
        with patch("src.services.podcast.load_prompt", return_value="prompt"), \
             patch.object(PodcastPipeline, "_select_items", return_value=_make_items(3)), \
             patch.object(PodcastPipeline, "_upload_and_update_feed") as mock_upload:

            mock_client.complete.return_value = CompletionResult(
                text=json.dumps({"script": VALID_SCRIPT}),
                usage=TokenUsage(),
            )

            with patch("src.services.podcast.TTSSynthesizer.synthesize") as mock_tts, \
                 patch("src.services.podcast.AudioMerger.merge") as mock_merge:
                fake_mp3 = Path("/tmp/fake.mp3")
                mock_tts.return_value = [Path("/tmp/seg.mp3")]
                mock_merge.return_value = fake_mp3

                pipeline = PodcastPipeline(config, mock_client)
                result = await pipeline.generate({"headlines": _make_items(3)}, "2026-03-13")

                mock_upload.assert_not_called()

    asyncio.run(_run())


def test_pipeline_r2_failure_doesnt_break_pipeline(tmp_path):
    """R2 upload failure doesn't prevent returning the local MP3."""
    mock_client = AsyncMock()
    config = PodcastConfig(
        enabled=True,
        r2_bucket="test-bucket",
        r2_endpoint="https://fake.r2.dev",
        r2_public_url="https://pub-abc.r2.dev",
    )

    fake_mp3 = tmp_path / "horizon-2026-03-13.mp3"
    fake_mp3.write_bytes(b"\xff" * 100)

    async def _run():
        with patch("src.services.podcast.load_prompt", return_value="prompt"), \
             patch.object(PodcastPipeline, "_select_items", return_value=_make_items(3)):

            mock_client.complete.return_value = CompletionResult(
                text=json.dumps({"script": VALID_SCRIPT}),
                usage=TokenUsage(),
            )

            with patch("src.services.podcast.TTSSynthesizer.synthesize") as mock_tts, \
                 patch("src.services.podcast.AudioMerger.merge") as mock_merge, \
                 patch.object(R2Uploader, "upload", side_effect=Exception("R2 down")):
                mock_tts.return_value = [tmp_path / "seg.mp3"]
                mock_merge.return_value = fake_mp3

                pipeline = PodcastPipeline(config, mock_client)
                result = await pipeline.generate({"headlines": _make_items(3)}, "2026-03-13")
                assert result == fake_mp3

    asyncio.run(_run())


def test_pipeline_uses_github_when_configured(tmp_path):
    """Pipeline calls GitHubUploader when github_repo is set."""
    mock_client = AsyncMock()
    config = PodcastConfig(
        enabled=True,
        github_repo="user/repo",
        github_pages_url="https://user.github.io/repo",
    )

    fake_mp3 = tmp_path / "horizon-2026-03-13.mp3"
    fake_mp3.write_bytes(b"\xff" * 100)

    async def _run():
        with patch("src.services.podcast.load_prompt", return_value="prompt"), \
             patch.object(PodcastPipeline, "_select_items", return_value=_make_items(3)), \
             patch.object(PodcastPipeline, "_upload_github") as mock_gh:
            mock_gh.return_value = None

            mock_client.complete.return_value = CompletionResult(
                text=json.dumps({"script": VALID_SCRIPT}),
                usage=TokenUsage(),
            )

            with patch("src.services.podcast.TTSSynthesizer.synthesize") as mock_tts, \
                 patch("src.services.podcast.AudioMerger.merge") as mock_merge:
                mock_tts.return_value = [tmp_path / "seg.mp3"]
                mock_merge.return_value = fake_mp3

                pipeline = PodcastPipeline(config, mock_client)
                result = await pipeline.generate({"headlines": _make_items(3)}, "2026-03-13")

                assert result == fake_mp3
                mock_gh.assert_called_once_with(fake_mp3, "2026-03-13")

    asyncio.run(_run())


def test_pipeline_github_preferred_over_r2(tmp_path):
    """GitHub is used when both github_repo and r2_bucket are set."""
    mock_client = AsyncMock()
    config = PodcastConfig(
        enabled=True,
        github_repo="user/repo",
        github_pages_url="https://user.github.io/repo",
        r2_bucket="also-set",
    )

    fake_mp3 = tmp_path / "test.mp3"
    fake_mp3.write_bytes(b"\xff" * 50)

    async def _run():
        with patch("src.services.podcast.load_prompt", return_value="prompt"), \
             patch.object(PodcastPipeline, "_select_items", return_value=_make_items(2)), \
             patch.object(PodcastPipeline, "_upload_github") as mock_gh, \
             patch.object(PodcastPipeline, "_upload_r2") as mock_r2:
            mock_gh.return_value = None

            mock_client.complete.return_value = CompletionResult(
                text=json.dumps({"script": VALID_SCRIPT}),
                usage=TokenUsage(),
            )

            with patch("src.services.podcast.TTSSynthesizer.synthesize") as mock_tts, \
                 patch("src.services.podcast.AudioMerger.merge") as mock_merge:
                mock_tts.return_value = [tmp_path / "seg.mp3"]
                mock_merge.return_value = fake_mp3

                pipeline = PodcastPipeline(config, mock_client)
                await pipeline.generate({"headlines": _make_items(2)}, "2026-03-13")

                mock_gh.assert_called_once()
                mock_r2.assert_not_called()

    asyncio.run(_run())
