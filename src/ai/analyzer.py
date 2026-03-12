"""Content analysis using AI."""

import json
import logging
import re
import time
from typing import Callable, List, Optional, Tuple
from tenacity import retry, stop_after_attempt, wait_exponential
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn

logger = logging.getLogger(__name__)

from .client import AIClient, TokenUsage, TokenUsageTracker
from .prompts import CONTENT_ANALYSIS_SYSTEM, CONTENT_ANALYSIS_USER
from ..models import ContentItem


class ContentAnalyzer:
    """Analyzes content items using AI to determine importance."""

    def __init__(self, ai_client: AIClient):
        self.client = ai_client

    @staticmethod
    def _parse_json_response(response: str) -> Optional[dict]:
        """Try multiple strategies to extract a JSON object from an AI response.

        Returns the parsed dict, or None if all strategies fail.
        """
        text = response.strip()

        # Strategy 1: direct parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: extract from ```json ... ``` code block
        if "```json" in text:
            try:
                json_str = text.split("```json")[1].split("```")[0].strip()
                return json.loads(json_str)
            except (json.JSONDecodeError, ValueError, IndexError):
                pass

        # Strategy 3: extract from ``` ... ``` code block
        if "```" in text:
            try:
                json_str = text.split("```")[1].split("```")[0].strip()
                return json.loads(json_str)
            except (json.JSONDecodeError, ValueError, IndexError):
                pass

        # Strategy 4: find the first { ... } block using brace matching
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start : i + 1])
                        except (json.JSONDecodeError, ValueError):
                            break

        # Strategy 5: regex extraction as last resort
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    async def analyze_batch(
        self,
        items: List[ContentItem],
        batch_size: int = 10,
        prompt_resolver: Optional[Callable[[ContentItem], str]] = None,
    ) -> Tuple[List[ContentItem], TokenUsage]:
        """Analyze items and return (analyzed_items, aggregated_token_usage).

        prompt_resolver: given a ContentItem, returns the system prompt to use.
        Falls back to CONTENT_ANALYSIS_SYSTEM when None.
        """
        logger.info("analyze_batch: starting %d items", len(items))
        t0 = time.monotonic()
        analyzed_items: List[ContentItem] = []
        tracker = TokenUsageTracker()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Analyzing", total=len(items))

            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                for item in batch:
                    system_prompt = prompt_resolver(item) if prompt_resolver else None
                    try:
                        usage = await self._analyze_item(item, system_prompt=system_prompt)
                        tracker.track(usage)
                        analyzed_items.append(item)
                    except Exception as e:
                        logger.error("Error analyzing item %s: %s", item.id, e)
                        item.ai_score = 0.0
                        item.ai_reason = "Analysis failed"
                        item.ai_summary = item.title
                        analyzed_items.append(item)
                    progress.advance(task)

        total_usage = TokenUsage(
            prompt_tokens=tracker.total_prompt_tokens,
            completion_tokens=tracker.total_completion_tokens,
        )
        logger.info("analyze_batch: completed %d items in %.1fs", len(items), time.monotonic() - t0)
        return analyzed_items, total_usage

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10)
    )
    async def _analyze_item(self, item: ContentItem, system_prompt: str = None) -> TokenUsage:
        """Analyze a single content item.

        Args:
            item: Content item to analyze (modified in-place)
            system_prompt: Override system prompt; defaults to CONTENT_ANALYSIS_SYSTEM

        Returns:
            TokenUsage from this single completion call.
        """
        system_prompt = system_prompt or CONTENT_ANALYSIS_SYSTEM

        content_section = ""
        if item.content:
            # Split off comments if present
            content_text = item.content
            if "--- Top Comments ---" in content_text:
                main, comments_part = content_text.split("--- Top Comments ---", 1)
                content_section = f"Content: {main.strip()[:800]}"
            else:
                content_section = f"Content: {content_text[:1000]}"

        # Prepare discussion section (comments, engagement)
        discussion_parts = []
        if item.content and "--- Top Comments ---" in item.content:
            comments_part = item.content.split("--- Top Comments ---", 1)[1]
            discussion_parts.append(f"Community Comments:\n{comments_part[:1500]}")

        meta = item.metadata
        engagement_items = []
        if meta.get("score"):
            engagement_items.append(f"score: {meta['score']}")
        if meta.get("descendants"):
            engagement_items.append(f"{meta['descendants']} comments")
        if meta.get("favorite_count"):
            engagement_items.append(f"{meta['favorite_count']} likes")
        if meta.get("retweet_count"):
            engagement_items.append(f"{meta['retweet_count']} retweets")
        if meta.get("reply_count"):
            engagement_items.append(f"{meta['reply_count']} replies")
        if meta.get("views"):
            engagement_items.append(f"{meta['views']} views")
        if meta.get("bookmarks"):
            engagement_items.append(f"{meta['bookmarks']} bookmarks")
        if meta.get("upvote_ratio"):
            engagement_items.append(f"upvote ratio: {meta['upvote_ratio']:.0%}")
        if engagement_items:
            discussion_parts.append(f"Engagement: {', '.join(engagement_items)}")
        if meta.get("discussion_url"):
            discussion_parts.append(f"Discussion: {meta['discussion_url']}")
        if meta.get("community_note"):
            discussion_parts.append(f"Community Note: {meta['community_note']}")

        discussion_section = "\n".join(discussion_parts) if discussion_parts else ""

        # Generate user prompt
        user_prompt = CONTENT_ANALYSIS_USER.format(
            title=item.title,
            source=f"{item.source_type.value}",
            author=item.author or "Unknown",
            url=str(item.url),
            content_section=content_section,
            discussion_section=discussion_section
        )

        completion = await self.client.complete(
            system=system_prompt,
            user=user_prompt,
            temperature=0.3
        )

        result = self._parse_json_response(completion.text)
        if result is None:
            logger.warning("Could not parse analysis response for %s, using defaults", item.id)
            item.ai_score = 0.0
            item.ai_reason = "Analysis response parse failed"
            item.ai_summary = item.title
            item.ai_tags = []
            return completion.usage

        item.ai_score = float(result.get("score", 0))
        item.ai_reason = result.get("reason", "")
        item.ai_summary = result.get("summary", item.title)
        item.ai_tags = result.get("tags", [])
        return completion.usage
