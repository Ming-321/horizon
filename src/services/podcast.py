"""Podcast generation pipeline: script → TTS → audio merge."""

import asyncio
import json
import logging
import math
import shutil
import tempfile
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
        return 500 <= total_chars <= 5000

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
            return result

        except Exception as e:
            logger.error("podcast pipeline failed: %s", e)
            return None
        finally:
            if work_dir and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

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
