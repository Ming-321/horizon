"""Content enrichment using AI (second-pass analysis).

For items that pass the score threshold, this module:
1. Searches the web for relevant context (via DuckDuckGo)
2. Feeds search results + item content to AI to generate grounded background knowledge
"""

import json
import logging
import re
import sys
import os
import time
from typing import List, Optional
from tenacity import retry, stop_after_attempt, wait_exponential
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn
from ddgs import DDGS

logger = logging.getLogger(__name__)

from .client import AIClient, TokenUsage, TokenUsageTracker
from .prompts import (
    CONCEPT_EXTRACTION_SYSTEM, CONCEPT_EXTRACTION_USER,
    CONTENT_ENRICHMENT_SYSTEM, CONTENT_ENRICHMENT_USER,
)
from ..models import ContentItem


_DEFAULT_SUMMARY_PROMPT = (
    "Summarize the following content changes. "
    'Output JSON: {"summary_en": "...", "summary_zh": "..."}'
)


class ContentEnricher:
    """Enriches high-scoring content items with background knowledge."""

    def __init__(self, ai_client: AIClient):
        self.client = ai_client

    async def enrich_batch(
        self, items: List[ContentItem], enrichment_system_prompt: str = None,
    ) -> TokenUsage:
        """Enrich items in-place with background knowledge.

        Returns aggregated TokenUsage for all AI calls during enrichment.
        """
        logger.info("enrich_batch: starting %d items", len(items))
        t0 = time.monotonic()
        tracker = TokenUsageTracker()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Enriching", total=len(items))

            for item in items:
                try:
                    usage = await self._enrich_item(
                        item, enrichment_system_prompt=enrichment_system_prompt,
                    )
                    if usage:
                        tracker.track(usage)
                except Exception as e:
                    logger.error("Error enriching item %s: %s", item.id, e)
                progress.advance(task)

        result = TokenUsage(
            prompt_tokens=tracker.total_prompt_tokens,
            completion_tokens=tracker.total_completion_tokens,
        )
        logger.info("enrich_batch: completed %d items in %.1fs", len(items), time.monotonic() - t0)
        return result

    async def summarize_batch(
        self, items: List[ContentItem], system_prompt: str = None,
    ) -> TokenUsage:
        """Light-weight enrichment: single AI call per item, no web search.

        Writes: metadata["detailed_summary_en"], metadata["detailed_summary_zh"],
                metadata["detailed_summary"].
        Does NOT write: concepts, search_results, background, community_discussion.
        """
        logger.info("summarize_batch: starting %d items", len(items))
        t0 = time.monotonic()
        prompt = system_prompt or _DEFAULT_SUMMARY_PROMPT
        tracker = TokenUsageTracker()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            transient=True,
        ) as progress:
            task = progress.add_task("Summarizing", total=len(items))

            for item in items:
                try:
                    usage = await self._summarize_item(item, system_prompt=prompt)
                    if usage:
                        tracker.track(usage)
                except Exception as e:
                    logger.error("Error summarizing item %s: %s", item.id, e)
                progress.advance(task)

        result = TokenUsage(
            prompt_tokens=tracker.total_prompt_tokens,
            completion_tokens=tracker.total_completion_tokens,
        )
        logger.info("summarize_batch: completed %d items in %.1fs", len(items), time.monotonic() - t0)
        return result

    async def _summarize_item(
        self, item: ContentItem, system_prompt: str = None,
    ) -> TokenUsage:
        """Single AI call: generate {summary_en, summary_zh} from item content."""
        prompt = system_prompt or _DEFAULT_SUMMARY_PROMPT
        content_text = (item.content or item.title)[:4000]

        user_prompt = (
            f"Title: {item.title}\n"
            f"URL: {item.url}\n\n"
            f"Content:\n{content_text}"
        )

        completion = await self.client.complete(
            system=prompt,
            user=user_prompt,
            temperature=0.3,
        )

        result = self._parse_json_response(completion.text)
        if result is None:
            logger.warning("Could not parse summary response for %s", item.id)
            return completion.usage

        item.metadata["detailed_summary_en"] = result.get("summary_en", "")
        item.metadata["detailed_summary_zh"] = result.get("summary_zh", "")
        item.metadata["detailed_summary"] = result.get("summary_en", "")

        return completion.usage

    async def _web_search(self, query: str, max_results: int = 3) -> list:
        """Search the web for context via DuckDuckGo.

        Returns:
            List of dicts with keys: title, url, body
        """
        try:
            # Suppress primp "Impersonate ... does not exist" stderr warning
            stderr = sys.stderr
            sys.stderr = open(os.devnull, "w")
            try:
                ddgs = DDGS()
                results = ddgs.text(query, max_results=max_results)
            finally:
                sys.stderr.close()
                sys.stderr = stderr
        except Exception:
            return []

        return [
            {"title": r.get("title", ""), "url": r.get("href", ""), "body": r.get("body", "")}
            for r in (results or [])
        ]

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

    async def _extract_concepts(self, item: ContentItem, content_text: str) -> tuple:
        """Ask AI to identify concepts that need explanation.

        Returns (queries, usage) tuple.
        """
        user_prompt = CONCEPT_EXTRACTION_USER.format(
            title=item.title,
            summary=item.ai_summary or item.title,
            tags=", ".join(item.ai_tags) if item.ai_tags else "",
            content=content_text[:1000],
        )

        try:
            completion = await self.client.complete(
                system=CONCEPT_EXTRACTION_SYSTEM,
                user=user_prompt,
                temperature=0.3,
            )
            result = self._parse_json_response(completion.text)
            if result is None:
                return [], completion.usage
            queries = result.get("queries", [])
            return queries[:3], completion.usage
        except Exception:
            return [], TokenUsage()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=10)
    )
    async def _enrich_item(
        self, item: ContentItem, enrichment_system_prompt: str = None,
    ) -> TokenUsage:
        """Enrich a single item with background knowledge. Returns combined TokenUsage."""
        # Extract content text and comments separately
        content_text = ""
        comments_text = ""
        if item.content:
            if "--- Top Comments ---" in item.content:
                main, comments_part = item.content.split("--- Top Comments ---", 1)
                content_text = main.strip()[:4000]
                comments_text = comments_part.strip()[:2000]
            else:
                content_text = item.content[:4000]

        combined_usage = TokenUsage()

        queries, concept_usage = await self._extract_concepts(item, content_text)
        combined_usage = TokenUsage(
            prompt_tokens=combined_usage.prompt_tokens + concept_usage.prompt_tokens,
            completion_tokens=combined_usage.completion_tokens + concept_usage.completion_tokens,
        )

        # Step 2: Search web for each concept
        all_results = []
        web_sections = []
        for query in queries:
            results = await self._web_search(query)
            all_results.extend(results)
            if results:
                lines = [f"- [{r['title']}]({r['url']}): {r['body']}" for r in results]
                web_sections.append(f"**{query}:**\n" + "\n".join(lines))
        web_context = "\n\n".join(web_sections) if web_sections else ""

        # Index of available URLs for citation validation
        available_urls = {r["url"]: r["title"] for r in all_results if r.get("url")}

        # Step 3: AI generates background grounded in search results
        user_prompt = CONTENT_ENRICHMENT_USER.format(
            title=item.title,
            url=str(item.url),
            summary=item.ai_summary or item.title,
            score=item.ai_score or 0,
            reason=item.ai_reason or "",
            tags=", ".join(item.ai_tags) if item.ai_tags else "",
            content=content_text,
            comments_section=f"\n**Community Comments:**\n{comments_text}" if comments_text else "",
            web_context=web_context or "No web search results available.",
        )

        system = enrichment_system_prompt or CONTENT_ENRICHMENT_SYSTEM
        completion = await self.client.complete(
            system=system,
            user=user_prompt,
            temperature=0.4,
        )

        combined_usage = TokenUsage(
            prompt_tokens=combined_usage.prompt_tokens + completion.usage.prompt_tokens,
            completion_tokens=combined_usage.completion_tokens + completion.usage.completion_tokens,
        )

        result = self._parse_json_response(completion.text)
        if result is None:
            logger.warning("Could not parse enrichment response for %s", item.id)
            return combined_usage

        # Combine structured sub-fields into per-language detailed_summary
        for lang in ("en", "zh"):
            if result.get(f"title_{lang}"):
                item.metadata[f"title_{lang}"] = result[f"title_{lang}"]

            parts = []
            for field in ("whats_new", "why_it_matters", "key_details"):
                text = result.get(f"{field}_{lang}", "").strip()
                if text:
                    parts.append(text)
            if parts:
                item.metadata[f"detailed_summary_{lang}"] = " ".join(parts)

            if result.get(f"background_{lang}"):
                item.metadata[f"background_{lang}"] = result[f"background_{lang}"]

            if result.get(f"community_discussion_{lang}"):
                item.metadata[f"community_discussion_{lang}"] = result[f"community_discussion_{lang}"]

        # Store citation sources — only URLs that actually came from our search results
        if result.get("sources") and available_urls:
            valid = [
                {"url": u, "title": available_urls[u]}
                for u in result["sources"]
                if u in available_urls
            ]
            if valid:
                item.metadata["sources"] = valid

        item.metadata["detailed_summary"] = item.metadata.get("detailed_summary_en", "")
        item.metadata["background"] = item.metadata.get("background_en", "")
        item.metadata["community_discussion"] = item.metadata.get("community_discussion_en", "")

        return combined_usage
