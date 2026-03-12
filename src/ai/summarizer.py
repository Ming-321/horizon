"""Daily summary generation — pure programmatic rendering."""

import re
from typing import List, Dict

from ..models import ContentItem


_CJK = r"[\u4e00-\u9fff\u3400-\u4dbf]"
_ASCII = r"[A-Za-z0-9]"


def _pangu(text: str) -> str:
    """Insert a space between CJK and ASCII letters/digits (Pangu spacing)."""
    text = re.sub(rf"({_CJK})({_ASCII})", r"\1 \2", text)
    text = re.sub(rf"({_ASCII})({_CJK})", r"\1 \2", text)
    return text


LABELS = {
    "en": {
        "header": "Horizon Daily",
        "source": "Source",
        "background": "Background",
        "discussion": "Discussion",
        "references": "References",
        "tags": "Tags",
        "empty_body": (
            "No significant developments today. This might indicate:\n"
            "- A quiet day in your tracked sources\n"
            "- The AI score threshold is too high\n"
            "- Your information sources need expansion\n\n"
            "Consider:\n"
            "1. Lowering the `ai_score_threshold` in config.json\n"
            "2. Adding more diverse information sources\n"
            "3. Checking if the AI model is working correctly\n"
        ),
    },
    "zh": {
        "header": "Horizon \u6bcf\u65e5\u901f\u9012",
        "source": "\u6765\u6e90",
        "background": "\u80cc\u666f",
        "discussion": "\u793e\u533a\u8ba8\u8bba",
        "references": "\u53c2\u8003\u94fe\u63a5",
        "tags": "\u6807\u7b7e",
        "empty_body": (
            "\u4eca\u65e5\u6682\u65e0\u91cd\u8981\u52a8\u6001\uff0c\u53ef\u80fd\u539f\u56e0\uff1a\n"
            "- \u4eca\u5929\u5173\u6ce8\u7684\u4fe1\u606f\u6e90\u8f83\u5e73\u9759\n"
            "- AI \u8bc4\u5206\u9608\u503c\u8bbe\u7f6e\u8fc7\u9ad8\n"
            "- \u4fe1\u606f\u6e90\u79cd\u7c7b\u6709\u5f85\u6269\u5145\n\n"
            "\u5efa\u8bae\uff1a\n"
            "1. \u5728 config.json \u4e2d\u964d\u4f4e `ai_score_threshold`\n"
            "2. \u6dfb\u52a0\u66f4\u591a\u591a\u6837\u5316\u7684\u4fe1\u606f\u6e90\n"
            "3. \u68c0\u67e5 AI \u6a21\u578b\u662f\u5426\u6b63\u5e38\u5de5\u4f5c\n"
        ),
    },
}


class DailySummarizer:
    """Generates daily Markdown summaries from pre-analyzed content items."""

    def __init__(self):
        pass

    async def generate_summary(
        self,
        grouped_items: Dict[str, List[ContentItem]],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        """Generate daily summary in Markdown format with group sections.

        Args:
            grouped_items: {group_name: [items]} ordered dict.
            date: Date string (YYYY-MM-DD)
            total_fetched: Total number of items fetched before filtering
            language: Output language, either "en" or "zh"

        Returns:
            str: Markdown formatted summary
        """
        labels = LABELS.get(language, LABELS["en"])

        all_items = [item for items_list in grouped_items.values() for item in items_list]

        if not all_items:
            return self._generate_empty_summary(date, total_fetched, labels)

        header = (
            f"# {labels['header']} - {date}\n\n"
            f"> From {total_fetched} items, {len(all_items)} important content pieces were selected\n\n"
            "---\n\n"
        )

        is_single_group = len(grouped_items) == 1

        toc_lines: List[str] = []
        global_idx = 0
        for group_name, g_items in grouped_items.items():
            if not g_items:
                continue
            if not is_single_group:
                toc_lines.append(f"### {group_name}")
            for item in g_items:
                global_idx += 1
                t = (item.metadata.get(f"title_{language}") or item.title).replace("[", "(").replace("]", ")")
                if language == "zh":
                    t = _pangu(t)
                score = item.ai_score or "?"
                toc_lines.append(f"{global_idx}. [{t}](#item-{global_idx}) \u2b50\ufe0f {score}/10")
            toc_lines.append("")
        toc = "\n".join(toc_lines) + "---\n\n"

        body_parts: List[str] = []
        item_idx = 0
        for group_name, g_items in grouped_items.items():
            if not g_items:
                continue
            if not is_single_group:
                body_parts.append(f"## {group_name}\n\n")
            for item in g_items:
                item_idx += 1
                body_parts.append(self._format_item(item, labels, language, item_idx))

        return header + toc + "".join(body_parts)

    def _format_item(self, item: ContentItem, labels: dict, language: str, index: int) -> str:
        """Format a single ContentItem into Markdown."""
        title = (
            item.metadata.get(f"title_{language}")
            or item.title
        ).replace("[", "(").replace("]", ")")
        url = str(item.url)
        score = item.ai_score or "?"
        meta = item.metadata

        summary = (
            meta.get(f"detailed_summary_{language}")
            or meta.get("detailed_summary")
            or item.ai_summary
            or ""
        )
        background = meta.get(f"background_{language}") or meta.get("background") or ""
        discussion = (
            meta.get(f"community_discussion_{language}")
            or meta.get("community_discussion")
            or ""
        )

        if language == "zh":
            title = _pangu(title)
            summary = _pangu(summary)
            background = _pangu(background)
            discussion = _pangu(discussion)

        source_type = item.source_type.value
        source_parts = [source_type]
        if meta.get("subreddit"):
            source_parts.append(f"r/{meta['subreddit']}")
        if meta.get("feed_name"):
            source_parts.append(meta["feed_name"])
        else:
            source_parts.append(item.author or "unknown")
        if item.published_at:
            day = item.published_at.strftime("%d").lstrip("0")
            source_parts.append(item.published_at.strftime(f"%b {day}, %H:%M"))
        source_line = " \u00b7 ".join(source_parts)

        lines = [
            f'<a id="item-{index}"></a>',
            f"## [{title}]({url}) \u2b50\ufe0f {score}/10",
            "",
            summary,
            "",
            source_line,
        ]

        if background:
            lines.append("")
            lines.append(f"**{labels['background']}**: {background}")

        sources = meta.get("sources") or []
        if sources:
            items_html = "".join(f'<li><a href="{s["url"]}">{s["title"]}</a></li>\n' for s in sources)
            lines += [
                "",
                f'<details><summary>{labels["references"]}</summary>\n<ul>\n{items_html}\n</ul>\n</details>',
            ]

        if discussion:
            lines.append("")
            lines.append(f"**{labels['discussion']}**: {discussion}")

        if item.ai_tags:
            tags_str = ", ".join([f"`#{t}`" for t in item.ai_tags])
            lines.append("")
            lines.append(f"**{labels['tags']}**: {tags_str}")

        lines.append("")
        lines.append("---")

        return "\n".join(lines) + "\n\n"

    def _generate_empty_summary(self, date: str, total_fetched: int, labels: dict) -> str:
        """Generate summary when no high-scoring items were found."""
        return (
            f"# {labels['header']} - {date}\n\n"
            f"> Analyzed {total_fetched} items, but none met the importance threshold.\n\n"
            + labels["empty_body"]
        )
