"""HtmlDetailRenderer — newspaper-style HTML report using Jinja2."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader

from ..models import ContentItem


class HtmlDetailRenderer:
    """Renders a self-contained newspaper-style HTML page (Chinese only)."""

    def __init__(self, template_dir: str = "data/templates"):
        self.env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=True,
        )
        self.template = self.env.get_template("daily_report.html")

    def render(
        self,
        grouped_items: Dict[str, List[ContentItem]],
        date: str,
        total_fetched: int,
    ) -> str:
        return self.template.render(
            date=date,
            total_fetched=total_fetched,
            total_selected=sum(len(items) for items in grouped_items.values()),
            groups=[
                {
                    "name": name,
                    "entries": [self._prepare_item(item) for item in items],
                }
                for name, items in grouped_items.items()
                if items
            ],
        )

    @staticmethod
    def _prepare_item(item: ContentItem) -> Dict[str, Any]:
        """Convert ContentItem to a template-friendly dict."""
        meta = item.metadata or {}

        published = ""
        if item.published_at:
            published = item.published_at.strftime("%Y-%m-%d %H:%M")

        feed_name = meta.get("feed_name") or meta.get("channel") or meta.get("subreddit") or ""
        source_parts = [item.source_type.value]
        if feed_name:
            source_parts.append(str(feed_name))
        if published:
            source_parts.append(published)

        sources_raw = meta.get("sources") or []
        sources = []
        for s in sources_raw:
            if isinstance(s, dict):
                sources.append(s)
            elif isinstance(s, str):
                sources.append({"url": s, "title": s})

        return {
            "title": meta.get("title_zh") or item.title,
            "url": str(item.url),
            "score": item.ai_score,
            "summary": (
                meta.get("detailed_summary_zh")
                or meta.get("detailed_summary")
                or item.ai_summary
                or ""
            ),
            "background": meta.get("background_zh") or meta.get("background") or "",
            "discussion": (
                meta.get("community_discussion_zh")
                or meta.get("community_discussion")
                or ""
            ),
            "sources": sources,
            "tags": item.ai_tags or [],
            "source_line": " · ".join(source_parts),
            "published_at": item.published_at,
        }
