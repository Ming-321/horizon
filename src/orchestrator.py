"""Main orchestrator coordinating the entire workflow."""

import asyncio
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse
import httpx
from rich.console import Console

logger = logging.getLogger(__name__)

from .models import Config, ContentItem, GroupConfig, ScoringConfig
from .storage.manager import StorageManager
from .services.emailer import EmailManager
from .scrapers.github import GitHubScraper
from .scrapers.hackernews import HackerNewsScraper
from .scrapers.rss import RSSScraper
from .scrapers.reddit import RedditScraper
from .scrapers.telegram import TelegramScraper
from .ai.client import create_ai_client, TokenUsageTracker
from .ai.analyzer import ContentAnalyzer
from .ai.summarizer import DailySummarizer
from .ai.enricher import ContentEnricher
from .ai.prompts import get_scoring_prompt, load_enrichment_prompt


@dataclass
class GroupBucket:
    """Intermediate container holding a group config and its routed items."""
    group: GroupConfig
    items: List[ContentItem] = field(default_factory=list)


class HorizonOrchestrator:
    """Orchestrates the complete workflow for content aggregation and analysis."""

    def __init__(self, config: Config, storage: StorageManager):
        self.config = config
        self.storage = storage
        self.console = Console()
        self.email_manager = EmailManager(config.email, console=self.console) if config.email else None

    async def run(self, force_hours: int = None, from_cache: bool = False) -> None:
        self.console.print("[bold cyan]\N{SUNRISE OVER MOUNTAINS} Horizon - Starting aggregation...[/bold cyan]\n")

        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

        if from_cache:
            cached = self.storage.load_grouped_items(today)
            if cached:
                self.console.print(f"[bold green]\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS} Loading from cache ({today})[/bold green]\n")
                grouped_for_summary = cached["groups"]
                total_fetched = cached["total_fetched"]
                total_items = sum(len(v) for v in grouped_for_summary.values())
                self.console.print(f"   {len(grouped_for_summary)} groups, {total_items} items (from cache)\n")
                return await self._render_outputs(grouped_for_summary, today, total_fetched)
            else:
                self.console.print("[yellow]No cache found for today, running full pipeline...[/yellow]\n")

        if self.email_manager and self.config.email and self.config.email.enabled:
            self.console.print("\N{ENVELOPE} Checking for new email subscriptions...")
            self.email_manager.check_subscriptions(self.storage)

        try:
            since = self._determine_time_window(force_hours)
            self.console.print(f"\N{CALENDAR} Fetching content since: {since.strftime('%Y-%m-%d %H:%M:%S')}\n")

            logger.info("fetch: starting...")
            t0 = time.monotonic()
            all_items = await self.fetch_all_sources(since)
            logger.info("fetch: completed %d items in %.1fs", len(all_items), time.monotonic() - t0)
            self.console.print(f"\N{INBOX TRAY} Fetched {len(all_items)} items from all sources\n")

            if not all_items:
                self.console.print("[yellow]No new content found. Exiting.[/yellow]")
                return

            logger.info("dedup: starting with %d items", len(all_items))
            t0 = time.monotonic()
            merged_items = self.merge_cross_source_duplicates(all_items)
            logger.info("dedup: %d → %d items in %.1fs", len(all_items), len(merged_items), time.monotonic() - t0)
            if len(merged_items) < len(all_items):
                self.console.print(
                    f"\N{LINK SYMBOL} Merged {len(all_items) - len(merged_items)} cross-source duplicates "
                    f"\N{RIGHTWARDS ARROW} {len(merged_items)} unique items\n"
                )

            logger.info("repo_aggregation: starting with %d items", len(merged_items))
            t0 = time.monotonic()
            before_agg = len(merged_items)
            merged_items = self._aggregate_repo_updates(merged_items)
            logger.info("repo_aggregation: %d → %d items in %.1fs", before_agg, len(merged_items), time.monotonic() - t0)
            if len(merged_items) < before_agg:
                self.console.print(
                    f"\N{PACKAGE} Aggregated repo updates: {before_agg} \N{RIGHTWARDS ARROW} {len(merged_items)} items\n"
                )

            tracker = TokenUsageTracker()

            logger.info("route: starting...")
            grouped = self._route_to_groups(merged_items)
            group_summary = ", ".join(f"{n}={len(b.items)}" for n, b in grouped.items())
            logger.info("route: %d groups [%s]", len(grouped), group_summary)

            ai_client = create_ai_client(self.config.ai)
            analyzer = ContentAnalyzer(ai_client)

            for name, bucket in grouped.items():
                if bucket.group.scoring.enabled:
                    total = len(bucket.items)
                    logger.info("[%s] scoring: starting %d items", name, total)
                    t0 = time.monotonic()
                    resolver = self._build_prompt_resolver(bucket.group)
                    analyzed, usage = await analyzer.analyze_batch(
                        bucket.items, prompt_resolver=resolver,
                    )
                    tracker.track(usage)
                    threshold = bucket.group.scoring.threshold
                    bucket.items = [
                        i for i in analyzed
                        if i.ai_score is not None and i.ai_score >= threshold
                    ]
                    bucket.items.sort(key=lambda x: x.ai_score or 0, reverse=True)
                    logger.info("[%s] scoring: %d/%d above threshold in %.1fs", name, len(bucket.items), total, time.monotonic() - t0)
                    self.console.print(
                        f"\N{WHITE MEDIUM STAR} [{name}] {len(bucket.items)} items scored \N{GREATER-THAN OR EQUAL TO} {threshold}\n"
                    )
                else:
                    logger.info("[%s] scoring: skipped (bypass), %d items", name, len(bucket.items))
                    self.console.print(
                        f"\N{TRIANGULAR FLAG ON POST} [{name}] {len(bucket.items)} items (bypass \N{EM DASH} no scoring)\n"
                    )

            for name, bucket in grouped.items():
                if bucket.group.scoring.enabled:
                    before = len(bucket.items)
                    bucket.items = self.merge_topic_duplicates(bucket.items)
                    removed = before - len(bucket.items)
                    if removed:
                        self.console.print(
                            f"\N{BROOM} [{name}] Removed {removed} topic duplicates "
                            f"\N{RIGHTWARDS ARROW} {len(bucket.items)} unique items\n"
                        )

            for name, bucket in grouped.items():
                selected_counts: Dict[str, int] = defaultdict(int)
                for item in bucket.items:
                    key = f"{item.source_type.value}/{self._sub_source_label(item)}"
                    selected_counts[key] += 1
                if selected_counts:
                    self.console.print(f"   [{name}]")
                    for source_key, count in sorted(selected_counts.items()):
                        self.console.print(f"      \N{BULLET} {source_key}: {count}")
            self.console.print("")

            ai_client_enrich = create_ai_client(self.config.ai)
            enricher = ContentEnricher(ai_client_enrich)
            _VALID_ENRICHMENT_MODES = {"full", "summary_only", "none"}
            for name, bucket in grouped.items():
                mode = bucket.group.enrichment_mode
                if mode not in _VALID_ENRICHMENT_MODES:
                    logger.error("[%s] invalid enrichment_mode '%s', skipping enrichment", name, mode)
                    continue
                if mode == "none":
                    logger.info("[%s] enrich: skipped (mode=none)", name)
                    self.console.print(f"   [{name}] skipped enrichment\n")
                    continue
                if not bucket.items:
                    logger.info("[%s] enrich: skipped (empty bucket)", name)
                    continue
                logger.info("[%s] enrich: starting %d items (mode=%s)", name, len(bucket.items), mode)
                t0 = time.monotonic()
                prompt = load_enrichment_prompt(bucket.group)
                if mode == "summary_only":
                    usage = await enricher.summarize_batch(
                        bucket.items, system_prompt=prompt,
                    )
                else:
                    usage = await enricher.enrich_batch(
                        bucket.items, enrichment_system_prompt=prompt,
                    )
                tracker.track(usage)
                logger.info("[%s] enrich: completed %d items in %.1fs", name, len(bucket.items), time.monotonic() - t0)
                self.console.print(
                    f"\N{BOOKS} [{name}] enriched {len(bucket.items)} items (mode={mode})\n"
                )

            grouped_for_summary: Dict[str, List[ContentItem]] = {}
            for name, bucket in grouped.items():
                if bucket.items:
                    grouped_for_summary[name] = bucket.items

            cache_path = self.storage.save_grouped_items(today, grouped_for_summary, len(all_items))
            self.console.print(f"\N{FLOPPY DISK} Cached enriched items to: {cache_path}\n")
            await self._render_outputs(grouped_for_summary, today, len(all_items))

            self.console.print(f"\N{BAR CHART} {tracker.summary()}")
            self.console.print("[bold green]\N{WHITE HEAVY CHECK MARK} Horizon completed successfully![/bold green]")

        except Exception as e:
            self.console.print(f"[bold red]\N{CROSS MARK} Error: {e}[/bold red]")
            raise

    async def _render_outputs(
        self,
        grouped_for_summary: Dict[str, List[ContentItem]],
        today: str,
        total_fetched: int,
    ) -> None:
        """Generate summaries, brief, HTML, and notifications from enriched items."""
        for lang in self.config.ai.languages:
            logger.info("summarize: starting for %s", lang)
            t0 = time.monotonic()
            summary = await self._generate_summary(
                grouped_for_summary, today, total_fetched, language=lang,
            )
            logger.info("summarize: completed for %s in %.1fs", lang, time.monotonic() - t0)

            summary_path = self.storage.save_daily_summary(today, summary, language=lang)
            self.console.print(f"\N{FLOPPY DISK} Saved {lang.upper()} summary to: {summary_path}\n")

            self._copy_to_jekyll(today, lang, summary)

            if self.email_manager and self.config.email and self.config.email.enabled:
                self.console.print(f"\N{ENVELOPE} Sending {lang.upper()} email summary...")
                subscribers = self.storage.load_subscribers()
                subject = f"Horizon Summary ({lang.upper()}) - {today}"
                self.email_manager.send_daily_summary(summary, subject, subscribers)

        brief_md = None
        if self.config.output.brief.enabled:
            try:
                from .renderers.brief import BriefRenderer
                brief_renderer = BriefRenderer(top_n=self.config.output.brief.top_n)
                brief_md = brief_renderer.render(grouped_for_summary, today)
                brief_path = self.storage.save_brief(today, brief_md)
                logger.info("brief: saved to %s", brief_path)
                self.console.print(f"\N{NEWSPAPER} Saved brief summary to: {brief_path}\n")
            except Exception as e:
                logger.warning("brief output failed: %s", e)
                self.console.print(f"[yellow]brief output failed: {e}[/yellow]\n")

        if self.config.notifications.wxpusher.enabled:
            try:
                import markdown as md_lib
                from .renderers.brief import BriefRenderer
                from .services.wxpusher import WxPusherService
                if brief_md is None:
                    brief_renderer = BriefRenderer(top_n=self.config.output.brief.top_n)
                    brief_md = brief_renderer.render(grouped_for_summary, today)
                wxpusher = WxPusherService(self.config.notifications.wxpusher)
                brief_html = md_lib.markdown(brief_md)
                summary_title = f"Horizon 每日速递 - {today}"
                if wxpusher.push(brief_html, summary=summary_title):
                    self.console.print(f"\N{BELL} Pushed brief to wxpusher\n")
                else:
                    self.console.print("[yellow]wxpusher push failed[/yellow]\n")
            except Exception as e:
                logger.warning("wxpusher push failed: %s", e)
                self.console.print(f"[yellow]wxpusher push error: {e}[/yellow]\n")

        if self.config.output.html.enabled:
            try:
                from .renderers.html_detail import HtmlDetailRenderer
                html_renderer = HtmlDetailRenderer()
                html_content = html_renderer.render(grouped_for_summary, today, total_fetched)
                html_path = self.storage.save_html(today, html_content)
                logger.info("html: saved to %s", html_path)
                self.console.print(f"\N{GLOBE WITH MERIDIANS} Saved HTML report to: {html_path}\n")
            except Exception as e:
                logger.warning("html output failed: %s", e)
                self.console.print(f"[yellow]html output failed: {e}[/yellow]\n")

        if self.config.output.podcast.enabled:
            try:
                from .services.podcast import PodcastPipeline
                ai_client_podcast = create_ai_client(self.config.ai)
                pipeline = PodcastPipeline(self.config.output.podcast, ai_client_podcast)
                audio_path = await pipeline.generate(grouped_for_summary, today)
                if audio_path:
                    logger.info("podcast: saved to %s", audio_path)
                    self.console.print(f"\N{STUDIO MICROPHONE} Podcast saved to: {audio_path}\n")
                else:
                    self.console.print("[yellow]Podcast generation returned no output[/yellow]\n")
            except Exception as e:
                logger.warning("podcast generation failed: %s", e)
                self.console.print(f"[yellow]Podcast generation failed: {e}[/yellow]\n")

    # ------------------------------------------------------------------
    # Group routing
    # ------------------------------------------------------------------

    def _route_to_groups(self, items: List[ContentItem]) -> Dict[str, GroupBucket]:
        """Route items to configured groups by category. Backward-compatible."""
        if not self.config.groups:
            default = GroupConfig(
                id="_default",
                name="Daily",
                scoring=ScoringConfig(threshold=self.config.filtering.ai_score_threshold),
            )
            return {"Daily": GroupBucket(group=default, items=list(items))}

        cat_to_group: Dict[str, str] = {}
        default_group_name: Optional[str] = None
        buckets: Dict[str, GroupBucket] = {}

        for g in self.config.groups:
            buckets[g.name] = GroupBucket(group=g)
            for cat in g.categories:
                cat_to_group[cat] = g.name
            if g.default:
                default_group_name = g.name

        if not default_group_name:
            self.console.print(
                "[yellow]\N{WARNING SIGN} No group marked as default=true; "
                "unmatched items will be dropped.[/yellow]"
            )

        dropped = 0
        for item in items:
            target = cat_to_group.get(item.category) if item.category else None
            if target is None:
                target = default_group_name
            if target and target in buckets:
                buckets[target].items.append(item)
            else:
                dropped += 1

        if dropped:
            self.console.print(
                f"[yellow]\N{WARNING SIGN} {dropped} items had no matching group "
                f"and were dropped (category not in any group).[/yellow]"
            )

        self.console.print("\N{PACKAGE} Group routing:")
        for name, bucket in buckets.items():
            self.console.print(f"   {name}: {len(bucket.items)} items")
        self.console.print("")

        return buckets

    # ------------------------------------------------------------------
    # Repo aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate_repo_updates(items: List[ContentItem]) -> List[ContentItem]:
        """Merge multiple commits from the same repo (commits.atom) into one item."""
        aggregated = []
        repo_buckets: Dict[str, List[ContentItem]] = {}
        for item in items:
            if item.category == "github-updates" and item.metadata.get("feed_name"):
                repo_buckets.setdefault(item.metadata["feed_name"], []).append(item)
            else:
                aggregated.append(item)
        for feed_name, repo_items in repo_buckets.items():
            if len(repo_items) == 1:
                aggregated.append(repo_items[0])
                continue
            repo_items.sort(
                key=lambda x: (x.published_at is not None, x.published_at.timestamp() if x.published_at else float("-inf")),
                reverse=True,
            )
            newest = repo_items[0]
            top_summaries = [
                it.title.split(": ", 1)[-1] if ": " in it.title else it.title
                for it in repo_items[:2]
            ]
            title = f"{feed_name}: {len(repo_items)} updates \u2014 {', '.join(top_summaries)}"
            content_lines = [
                f"- [{it.published_at.strftime('%H:%M') if it.published_at else '??:??'}] {it.title}"
                for it in repo_items
            ]
            merged = ContentItem(
                id=newest.id,
                source_type=newest.source_type,
                title=title,
                url=newest.url,
                content="\n".join(content_lines),
                author=newest.author,
                published_at=newest.published_at,
                category=newest.category,
                metadata={**newest.metadata, "commit_count": len(repo_items)},
            )
            aggregated.append(merged)
        return aggregated

    # ------------------------------------------------------------------
    # Prompt resolution
    # ------------------------------------------------------------------

    def _build_source_prompt_map(self) -> Dict[str, Optional[str]]:
        """Build category -> scoring_prompt_file mapping from all source entries.

        When multiple entries share a category but differ in prompt, the last one wins
        and a warning is logged.
        """
        import logging
        logger = logging.getLogger(__name__)

        mapping: Dict[str, Optional[str]] = {}
        sources = self.config.sources

        def _set(cat: str, prompt_file: str) -> None:
            if cat in mapping and mapping[cat] != prompt_file:
                logger.warning(
                    "Category '%s' has conflicting scoring_prompt_file: '%s' overrides '%s'",
                    cat, prompt_file, mapping[cat],
                )
            mapping[cat] = prompt_file

        for gh in sources.github:
            if gh.category and gh.scoring_prompt_file:
                _set(gh.category, gh.scoring_prompt_file)

        hn = sources.hackernews
        if hn.scoring_prompt_file:
            _set(hn.category, hn.scoring_prompt_file)

        for rss in sources.rss:
            if rss.category and rss.scoring_prompt_file:
                _set(rss.category, rss.scoring_prompt_file)

        for sub in sources.reddit.subreddits:
            if sub.scoring_prompt_file:
                _set(sub.category, sub.scoring_prompt_file)
        for user in sources.reddit.users:
            if user.scoring_prompt_file:
                _set(user.category, user.scoring_prompt_file)

        for ch in sources.telegram.channels:
            if ch.scoring_prompt_file:
                _set(ch.category, ch.scoring_prompt_file)

        return mapping

    def _build_prompt_resolver(self, group: GroupConfig) -> Callable[[ContentItem], str]:
        source_prompt_map = self._build_source_prompt_map()

        def resolver(item: ContentItem) -> str:
            return get_scoring_prompt(
                source_prompt_file=source_prompt_map.get(item.category) if item.category else None,
                group_prompt_file=group.scoring.prompt_file,
            )
        return resolver

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _determine_time_window(self, force_hours: int = None) -> datetime:
        if force_hours:
            since = datetime.now(timezone.utc) - timedelta(hours=force_hours)
        else:
            hours = self.config.filtering.time_window_hours
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
        return since

    async def fetch_all_sources(self, since: datetime) -> List[ContentItem]:
        """Fetch content from all configured sources.

        This is a stable stage entry point for integrations such as MCP.
        """
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Horizon/1.0; +https://github.com/Thysrael/Horizon)"}
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            tasks = []

            if self.config.sources.github:
                github_scraper = GitHubScraper(self.config.sources.github, client)
                tasks.append(self._fetch_with_progress("GitHub", github_scraper, since))

            if self.config.sources.hackernews.enabled:
                hn_scraper = HackerNewsScraper(self.config.sources.hackernews, client)
                tasks.append(self._fetch_with_progress("Hacker News", hn_scraper, since))

            if self.config.sources.rss:
                rss_scraper = RSSScraper(self.config.sources.rss, client)
                tasks.append(self._fetch_with_progress("RSS Feeds", rss_scraper, since))

            if self.config.sources.reddit.enabled:
                reddit_scraper = RedditScraper(self.config.sources.reddit, client)
                tasks.append(self._fetch_with_progress("Reddit", reddit_scraper, since))

            if self.config.sources.telegram.enabled:
                telegram_scraper = TelegramScraper(self.config.sources.telegram, client)
                tasks.append(self._fetch_with_progress("Telegram", telegram_scraper, since))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            all_items = []
            for result in results:
                if isinstance(result, Exception):
                    self.console.print(f"[red]Error fetching source: {result}[/red]")
                elif isinstance(result, list):
                    all_items.extend(result)

            return all_items

    async def _fetch_with_progress(self, name: str, scraper, since: datetime) -> List[ContentItem]:
        self.console.print(f"\N{LEFT-POINTING MAGNIFYING GLASS} Fetching from {name}...")
        items = await scraper.fetch(since)
        self.console.print(f"   Found {len(items)} items from {name}")

        sub_counts: Dict[str, int] = defaultdict(int)
        for item in items:
            sub_counts[self._sub_source_label(item)] += 1
        if len(sub_counts) > 1:
            for sub, count in sorted(sub_counts.items()):
                self.console.print(f"      \N{BULLET} {sub}: {count}")

        return items

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _sub_source_label(item: ContentItem) -> str:
        meta = item.metadata
        if meta.get("subreddit"):
            return f"r/{meta['subreddit']}"
        if meta.get("feed_name"):
            return meta["feed_name"]
        if meta.get("channel"):
            return f"@{meta['channel']}"
        if meta.get("repo"):
            return meta["repo"]
        return item.author or "unknown"

    def merge_cross_source_duplicates(self, items: List[ContentItem]) -> List[ContentItem]:
        """Merge items that point to the same URL from different sources.

        This is a stable stage helper for integrations such as MCP.
        """
        def normalize_url(url: str) -> str:
            parsed = urlparse(str(url))
            host = parsed.hostname or ""
            if host.startswith("www."):
                host = host[4:]
            path = parsed.path.rstrip("/")
            return f"{host}{path}"

        url_groups: Dict[str, List[ContentItem]] = {}
        for item in items:
            key = normalize_url(str(item.url))
            url_groups.setdefault(key, []).append(item)

        merged = []
        for key, group in url_groups.items():
            if len(group) == 1:
                merged.append(group[0])
                continue

            primary = max(group, key=lambda x: len(x.content or ""))

            all_sources = set()
            for item in group:
                all_sources.add(item.source_type.value)
                for mk, mv in item.metadata.items():
                    if mk not in primary.metadata or not primary.metadata[mk]:
                        primary.metadata[mk] = mv

                if item is not primary and item.content:
                    if primary.content and item.content not in primary.content:
                        primary.content = (primary.content or "") + f"\n\n--- From {item.source_type.value} ---\n" + item.content

            primary.metadata["merged_sources"] = list(all_sources)
            merged.append(primary)

        return merged

    @staticmethod
    def _title_tokens(title: str) -> set:
        tokens = set()
        for w in re.findall(r'[a-zA-Z]{3,}', title):
            tokens.add(w.lower())
        cjk = re.sub(r'[^\u4e00-\u9fff]', '', title)
        for i in range(len(cjk) - 1):
            tokens.add(cjk[i:i + 2])
        return tokens

    @staticmethod
    def _merge_item_content(primary: ContentItem, secondary: ContentItem) -> None:
        if not secondary.content:
            return
        if secondary.content in (primary.content or ""):
            return
        label = secondary.source_type.value
        primary.content = (primary.content or "") + f"\n\n--- From {label} ---\n{secondary.content}"

    def merge_topic_duplicates(
        self, items: List[ContentItem], threshold: float = 0.33
    ) -> List[ContentItem]:
        """Merge items covering the same topic into the highest-scored one.

        This is a stable stage helper for integrations such as MCP.
        """
        kept: List[ContentItem] = []
        for item in items:
            tokens = self._title_tokens(item.title)
            item_tags = set(item.ai_tags or [])
            merged_into = None
            for accepted in kept:
                a_tokens = self._title_tokens(accepted.title)
                union = a_tokens | tokens
                title_sim = len(a_tokens & tokens) / len(union) if union else 0.0
                tag_overlap = len(set(accepted.ai_tags or []) & item_tags)
                if title_sim >= threshold or (tag_overlap >= 2 and title_sim >= 0.15):
                    merged_into = accepted
                    self.console.print(
                        f"   [dim]dedup: title_sim={title_sim:.2f} tag_overlap={tag_overlap}[/dim]\n"
                        f"   [dim]  keep : {accepted.title}[/dim]\n"
                        f"   [dim]  merge: {item.title}[/dim]"
                    )
                    break
            if merged_into is not None:
                self._merge_item_content(merged_into, item)
            else:
                kept.append(item)
        return kept

    # ------------------------------------------------------------------
    # AI enrichment & summary
    # ------------------------------------------------------------------

    async def _enrich_important_items(self, items: List[ContentItem], tracker: TokenUsageTracker = None) -> None:
        if not items:
            return
        self.console.print("\N{BOOKS} Enriching with background knowledge...")
        ai_client = create_ai_client(self.config.ai)
        enricher = ContentEnricher(ai_client)
        enrich_usage = await enricher.enrich_batch(items)
        if tracker:
            tracker.track(enrich_usage)
        self.console.print(f"   Enriched {len(items)} items\n")

    async def _analyze_content(self, items: List[ContentItem]) -> List[ContentItem]:
        """Legacy flat-pipeline analysis (used by MCP adapter)."""
        self.console.print("\N{ROBOT FACE} Analyzing content with AI...")
        ai_client = create_ai_client(self.config.ai)
        analyzer = ContentAnalyzer(ai_client)
        analyzed, _usage = await analyzer.analyze_batch(items)
        return analyzed

    async def _generate_summary(
        self,
        grouped_items: Dict[str, List[ContentItem]],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        self.console.print("\N{MEMO} Generating daily summary...")
        summarizer = DailySummarizer()
        return await summarizer.generate_summary(grouped_items, date, total_fetched, language=language)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _copy_to_jekyll(self, today: str, lang: str, summary: str) -> None:
        try:
            from pathlib import Path

            post_filename = f"{today}-summary-{lang}.md"
            posts_dir = Path("docs/_posts")
            posts_dir.mkdir(parents=True, exist_ok=True)

            dest_path = posts_dir / post_filename

            front_matter = (
                "---\n"
                "layout: default\n"
                f"title: \"Horizon Summary: {today} ({lang.upper()})\"\n"
                f"date: {today}\n"
                f"lang: {lang}\n"
                "---\n\n"
            )

            summary_content = summary
            first_line = summary_content.strip().split("\n")[0]
            if first_line.startswith("# "):
                parts = summary_content.split("\n", 1)
                if len(parts) > 1:
                    summary_content = parts[1].strip()

            with open(dest_path, "w", encoding="utf-8") as f:
                f.write(front_matter + summary_content)

            self.console.print(f"\N{PAGE FACING UP} Copied {lang.upper()} summary to GitHub Pages: {dest_path}\n")
        except Exception as e:
            self.console.print(f"[yellow]\N{WARNING SIGN}\ufe0f  Failed to copy {lang.upper()} summary to docs/: {e}[/yellow]\n")
