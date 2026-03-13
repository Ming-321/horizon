"""BriefRenderer — concise Markdown summary for mobile / WxPusher push."""

import re
from typing import Dict, List

from ..models import ContentItem

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])")


def _first_sentence(text: str, max_len: int = 80) -> str:
    """Extract the first sentence, falling back to truncated full text."""
    parts = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)
    first = parts[0].strip() if parts else ""
    if not first:
        return text[:max_len].rstrip() + ("…" if len(text) > max_len else "")
    if len(first) > max_len:
        return first[:max_len].rstrip() + "…"
    return first


class BriefRenderer:
    """Renders a concise per-group Markdown summary (Top N items)."""

    def __init__(self, top_n: int = 10):
        self.top_n = top_n

    def render(
        self,
        grouped_items: Dict[str, List[ContentItem]],
        date: str,
    ) -> str:
        lines: List[str] = [f"# Horizon 每日速递 - {date}", ""]

        for group_name, items in grouped_items.items():
            if not items:
                continue
            top_items = items[: self.top_n]
            lines.append(f"## {group_name}")
            for idx, item in enumerate(top_items, 1):
                title = self._get_title(item)
                summary = self._get_summary(item)
                score_part = f"⭐ {item.ai_score:.1f} | " if item.ai_score is not None else ""
                lines.append(f"{idx}. {score_part}[{title}]({item.url}) — {summary}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _get_title(item: ContentItem) -> str:
        meta = item.metadata
        return meta.get("title_zh") or item.title

    @staticmethod
    def _get_summary(item: ContentItem) -> str:
        meta = item.metadata
        return (
            meta.get("detailed_summary_zh")
            or meta.get("detailed_summary")
            or item.ai_summary
            or item.title
        )
