"""Podcast generation pipeline: script → TTS → audio merge → R2 upload → RSS feed."""

import asyncio
import json
import logging
import math
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ai.client import AIClient, CompletionResult
from ..ai.prompts import load_prompt
from ..models import ContentItem, PodcastConfig
from ..renderers.topic_classifier import TopicClassifier

logger = logging.getLogger(__name__)


class ScriptGenerator:
    """Generate a two-person dialogue script via LLM."""

    def __init__(self, ai_client: AIClient, prompt_text: str, max_chars: int):
        self.ai_client = ai_client
        self.prompt_text = prompt_text
        self.max_chars = max_chars

    async def generate(self, items: List[ContentItem], date: str) -> List[Dict[str, str]]:
        """Call LLM to produce a dialogue script from news items."""
        user_msg = self._build_user_message(items, date)
        try:
            completion: CompletionResult = await self.ai_client.complete(
                system=self.prompt_text,
                user=user_msg,
            )
            data = json.loads(completion.text)
            script = data["script"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to parse script JSON: %s", e)
            return []

        if not self._validate_script(script):
            logger.warning("Script validation failed")
            return []
        return script

    @staticmethod
    def _validate_script(script: List[Dict]) -> bool:
        """Validate speaker legality, non-empty text, min segments, total char range."""
        if not isinstance(script, list) or len(script) < 4:
            return False
        total_chars = 0
        for seg in script:
            if not isinstance(seg, dict):
                return False
            if seg.get("speaker") not in ("A", "B"):
                return False
            text = seg.get("text", "")
            if not text or not text.strip():
                return False
            total_chars += len(text)
        return 500 <= total_chars <= 8000

    def _build_user_message(self, items: List[ContentItem], date: str) -> str:
        lines = [f"日期：{date}", f"共 {len(items)} 条新闻素材：", ""]
        for i, item in enumerate(items, 1):
            summary = item.ai_summary or item.title
            tags = ", ".join(item.ai_tags[:5]) if item.ai_tags else ""
            lines.append(f"{i}. 【{item.title}】")
            lines.append(f"   摘要：{summary}")
            if tags:
                lines.append(f"   标签：{tags}")
            lines.append("")
        lines.append(f"请生成总字数在 1500-{self.max_chars} 之间的双人对话脚本。")
        return "\n".join(lines)


class TTSSynthesizer:
    """Synthesize speech segments using DashScope CosyVoice."""

    MAX_CHARS_PER_SEGMENT = 20000

    def __init__(self, model: str, endpoint: str, voice_map: Dict[str, str]):
        self.model = model
        self.endpoint = endpoint
        self.voice_map = voice_map

    async def synthesize(self, script: List[Dict[str, str]], work_dir: Path) -> List[Path]:
        """Synthesize each script segment to MP3 files. Skips failed segments."""
        import dashscope
        dashscope.base_websocket_api_url = self.endpoint

        api_key = self._get_api_key()

        segments: List[Path] = []
        for idx, seg in enumerate(script):
            speaker = seg["speaker"]
            text = seg["text"]
            if len(text) > self.MAX_CHARS_PER_SEGMENT:
                logger.warning("Segment %d exceeds %d chars, truncating", idx, self.MAX_CHARS_PER_SEGMENT)
                text = text[:self.MAX_CHARS_PER_SEGMENT]

            voice = self.voice_map.get(speaker, self.voice_map.get("A", "longanyang"))
            try:
                audio_bytes = await asyncio.to_thread(
                    self._synthesize_one, text, voice, api_key,
                )
                if audio_bytes:
                    out_path = work_dir / f"seg_{idx:04d}.mp3"
                    out_path.write_bytes(audio_bytes)
                    segments.append(out_path)
                else:
                    logger.warning("TTS segment %d returned empty audio", idx)
            except Exception as e:
                logger.warning("TTS segment %d failed: %s", idx, e)
        return segments

    def _synthesize_one(self, text: str, voice: str, api_key: str) -> Optional[bytes]:
        from dashscope.audio.tts_v2 import SpeechSynthesizer
        synth = SpeechSynthesizer(model=self.model, voice=voice)
        audio = synth.call(text)
        return audio if isinstance(audio, bytes) and len(audio) > 0 else None

    @staticmethod
    def _get_api_key() -> str:
        import os
        key = os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            raise EnvironmentError("DASHSCOPE_API_KEY not set")
        return key


class AudioMerger:
    """Merge audio segments using FFmpeg."""

    @staticmethod
    async def merge(segments: List[Path], output: Path) -> Optional[Path]:
        """Concatenate MP3 segments into a single file via ffmpeg -f concat."""
        if not segments:
            return None

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise FileNotFoundError("ffmpeg not found in PATH")

        concat_file = segments[0].parent / "concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{seg.resolve()}'" for seg in segments),
            encoding="utf-8",
        )

        output.parent.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-c", "copy", str(output),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("ffmpeg merge failed: %s", stderr.decode(errors="replace"))
            return None
        return output


class GitHubUploader:
    """Upload podcast episodes via GitHub Releases API."""

    GITHUB_API = "https://api.github.com"

    def __init__(self, repo: str, pages_url: str):
        self.repo = repo
        self.pages_url = pages_url.rstrip("/")
        self.token = os.environ.get("GITHUB_TOKEN", "")
        if not self.token:
            raise EnvironmentError("GITHUB_TOKEN not set")

    async def create_release_and_upload(self, local_path: Path, tag: str) -> str:
        """Create a GitHub release and upload the MP3 as an asset."""
        import httpx

        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            release_resp = await client.post(
                f"{self.GITHUB_API}/repos/{self.repo}/releases",
                headers=headers,
                json={"tag_name": tag, "name": tag, "body": f"Podcast episode {tag}"},
            )

            if release_resp.status_code == 422:
                existing = await client.get(
                    f"{self.GITHUB_API}/repos/{self.repo}/releases/tags/{tag}",
                    headers=headers,
                )
                if existing.status_code != 200:
                    raise RuntimeError(f"Failed to get existing release: {existing.text}")
                release_data = existing.json()
            elif release_resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to create release: {release_resp.text}")
            else:
                release_data = release_resp.json()

            upload_url = release_data["upload_url"].split("{")[0]
            file_name = local_path.name
            file_bytes = await asyncio.to_thread(local_path.read_bytes)

            upload_resp = await client.post(
                upload_url,
                params={"name": file_name},
                headers={**headers, "Content-Type": "application/octet-stream"},
                content=file_bytes,
            )
            if upload_resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to upload asset: {upload_resp.text}")

            return upload_resp.json()["browser_download_url"]

    def get_feed_path(self) -> Path:
        """Return local path for feed.xml in docs/ directory."""
        return Path("docs/podcast/feed.xml")

    def get_feed_url(self) -> str:
        return f"{self.pages_url}/podcast/feed.xml"


class R2Uploader:
    """Upload files to Cloudflare R2 via S3-compatible API."""

    def __init__(self, bucket: str, endpoint: str, public_url: str):
        self.bucket = bucket
        self.endpoint = endpoint
        self.public_url = public_url.rstrip("/")

    def _get_client(self):
        import boto3
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", ""),
        )

    async def upload(self, local_path: Path, remote_key: str,
                     content_type: str = "application/octet-stream") -> str:
        """Upload a file and return its public URL."""
        client = self._get_client()
        await asyncio.to_thread(
            client.upload_file,
            str(local_path), self.bucket, remote_key,
            ExtraArgs={"ContentType": content_type},
        )
        return f"{self.public_url}/{remote_key}"

    async def download_text(self, remote_key: str) -> Optional[str]:
        """Download a text file from R2. Returns None if not found."""
        client = self._get_client()
        try:
            resp = await asyncio.to_thread(
                client.get_object, Bucket=self.bucket, Key=remote_key,
            )
            return resp["Body"].read().decode("utf-8")
        except Exception:
            return None


class FeedGenerator:
    """Generate and update Podcast RSS 2.0 XML feeds."""

    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"

    def __init__(self, title: str, description: str, public_url: str):
        self.title = title
        self.description = description
        self.public_url = public_url.rstrip("/")

    def update_feed(self, date: str, audio_url: str, audio_size: int,
                    existing_xml: Optional[str] = None) -> str:
        """Create or update the RSS feed with a new episode."""
        if existing_xml:
            try:
                root = ET.fromstring(existing_xml)
                channel = root.find("channel")
            except ET.ParseError:
                logger.warning("Failed to parse existing feed, creating new")
                root, channel = self._new_feed()
        else:
            root, channel = self._new_feed()

        item = self._make_item(date, audio_url, audio_size)

        first_item = channel.find("item")
        if first_item is not None:
            items = list(channel.findall("item"))
            channel.remove(items[0]) if False else None  # noqa: keep reference
            idx = list(channel).index(first_item)
            channel.insert(idx, item)
        else:
            channel.append(item)

        ET.indent(root, space="  ")
        xml_decl = '<?xml version="1.0" encoding="UTF-8"?>\n'
        return xml_decl + ET.tostring(root, encoding="unicode")

    def _new_feed(self):
        root = ET.Element("rss", version="2.0")
        root.set("xmlns:itunes", self.ITUNES_NS)
        channel = ET.SubElement(root, "channel")
        ET.SubElement(channel, "title").text = self.title
        ET.SubElement(channel, "link").text = self.public_url
        ET.SubElement(channel, "description").text = self.description
        ET.SubElement(channel, "language").text = "zh-cn"
        ET.SubElement(channel, "generator").text = "Horizon Podcast Pipeline"
        return root, channel

    def _make_item(self, date: str, audio_url: str, audio_size: int):
        item = ET.Element("item")
        ET.SubElement(item, "title").text = f"{self.title} - {date}"
        ET.SubElement(item, "description").text = f"{date} 每日科技新闻播客"
        enclosure = ET.SubElement(item, "enclosure")
        enclosure.set("url", audio_url)
        enclosure.set("length", str(audio_size))
        enclosure.set("type", "audio/mpeg")
        ET.SubElement(item, "guid").text = audio_url
        pub_dt = datetime.strptime(date, "%Y-%m-%d").replace(
            hour=7, tzinfo=timezone(timedelta(hours=8)),
        )
        ET.SubElement(item, "pubDate").text = format_datetime(pub_dt)
        return item


class PodcastPipeline:
    """Orchestrate the full podcast generation pipeline."""

    def __init__(self, config: PodcastConfig, ai_client: AIClient):
        self.config = config
        self.ai_client = ai_client
        self.classifier = TopicClassifier(ai_client)

        prompt_text = load_prompt(config.prompt_file)
        if not prompt_text:
            raise ValueError(f"Podcast prompt file not found: {config.prompt_file}")
        self.script_gen = ScriptGenerator(ai_client, prompt_text, config.max_script_chars)

        self.tts = TTSSynthesizer(
            model=config.tts_model,
            endpoint=config.tts_endpoint,
            voice_map={"A": config.voice_a, "B": config.voice_b},
        )

    async def generate(
        self,
        grouped_items: Dict[str, List[ContentItem]],
        date: str,
    ) -> Optional[Path]:
        """Run full pipeline: select → script → TTS → merge."""
        work_dir = None
        try:
            selected = await self._select_items(grouped_items)
            if not selected:
                logger.info("podcast: no items selected, skipping")
                return None

            logger.info("podcast: selected %d items for script", len(selected))

            script = await self.script_gen.generate(selected, date)
            if not script:
                logger.warning("podcast: script generation returned empty")
                return None
            logger.info("podcast: script generated with %d segments", len(script))

            work_dir = Path(tempfile.mkdtemp(prefix="horizon_podcast_"))
            segments = await self.tts.synthesize(script, work_dir)
            if not segments:
                logger.warning("podcast: all TTS segments failed")
                return None
            logger.info("podcast: %d/%d TTS segments succeeded", len(segments), len(script))

            output_dir = Path(self.config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"horizon-{date}.mp3"

            result = await AudioMerger.merge(segments, output_path)

            if result and (self.config.github_repo or self.config.r2_bucket):
                await self._upload_and_update_feed(result, date)

            return result

        except Exception as e:
            logger.error("podcast pipeline failed: %s", e)
            return None
        finally:
            if work_dir and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    async def _upload_and_update_feed(self, audio_path: Path, date: str) -> None:
        """Upload MP3 and update RSS feed. Supports GitHub or R2 backends."""
        try:
            if self.config.github_repo:
                await self._upload_github(audio_path, date)
            elif self.config.r2_bucket:
                await self._upload_r2(audio_path, date)
        except Exception as e:
            logger.warning("podcast: upload/feed failed: %s", e)

    async def _upload_github(self, audio_path: Path, date: str) -> None:
        gh = GitHubUploader(self.config.github_repo, self.config.github_pages_url)
        tag = f"podcast-{date}"
        audio_url = await gh.create_release_and_upload(audio_path, tag)
        logger.info("podcast: uploaded to GitHub Release %s", audio_url)

        feed_path = gh.get_feed_path()
        feed_path.parent.mkdir(parents=True, exist_ok=True)

        existing_xml = None
        if feed_path.exists():
            existing_xml = feed_path.read_text(encoding="utf-8")

        feed_gen = FeedGenerator(
            self.config.feed_title,
            self.config.feed_description,
            self.config.github_pages_url,
        )
        feed_xml = feed_gen.update_feed(
            date, audio_url, audio_path.stat().st_size, existing_xml,
        )
        feed_path.write_text(feed_xml, encoding="utf-8")

        await self._git_push_feed(feed_path)
        logger.info("podcast: feed.xml pushed to GitHub Pages → %s", gh.get_feed_url())

    async def _upload_r2(self, audio_path: Path, date: str) -> None:
        uploader = R2Uploader(
            self.config.r2_bucket,
            self.config.r2_endpoint,
            self.config.r2_public_url,
        )
        remote_key = f"episodes/horizon-{date}.mp3"
        audio_url = await uploader.upload(audio_path, remote_key, "audio/mpeg")
        logger.info("podcast: uploaded to R2 %s", audio_url)

        existing = await uploader.download_text("feed.xml")
        feed_gen = FeedGenerator(
            self.config.feed_title,
            self.config.feed_description,
            self.config.r2_public_url,
        )
        feed_xml = feed_gen.update_feed(
            date, audio_url, audio_path.stat().st_size, existing,
        )
        feed_local = audio_path.parent / "feed.xml"
        feed_local.write_text(feed_xml, encoding="utf-8")
        await uploader.upload(feed_local, "feed.xml", "application/xml")
        logger.info("podcast: feed.xml updated on R2")

    @staticmethod
    async def _git_push_feed(feed_path: Path) -> None:
        """Commit and push feed.xml to update GitHub Pages."""
        cmds = [
            ["git", "add", str(feed_path)],
            ["git", "commit", "-m", "chore(podcast): update feed.xml"],
            ["git", "push"],
        ]
        for cmd in cmds:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode(errors="replace")
                if "nothing to commit" in err_msg:
                    continue
                logger.warning("podcast: git command failed: %s → %s", cmd, err_msg)

    async def _select_items(
        self,
        grouped_items: Dict[str, List[ContentItem]],
    ) -> List[ContentItem]:
        """Two-level balanced topic selection across groups and sub-topics."""
        all_selected: List[ContentItem] = []

        for group_name, items in grouped_items.items():
            if not items:
                continue

            has_scores = any(it.ai_score is not None for it in items)

            if not has_scores:
                all_selected.extend(items)
                continue

            item_dicts = [
                {"title": it.title, "tags": it.ai_tags or []}
                for it in items
            ]
            topics = await self.classifier.classify_group(group_name, item_dicts)

            for topic in topics:
                indices = topic.get("item_indices", [])
                topic_items = [items[i] for i in indices if i < len(items)]
                topic_items.sort(key=lambda x: x.ai_score or 0, reverse=True)
                take_n = max(self.config.items_per_group, math.ceil(len(topic_items) * 0.3))
                take_n = max(take_n, 1)
                all_selected.extend(topic_items[:take_n])

        all_selected.sort(key=lambda x: x.ai_score or 0, reverse=True)
        return all_selected
