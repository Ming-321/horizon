"""TopicClassifier — one AI call to cluster items into sub-topics per group."""

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REFERENCE_TOPICS = [
    "模型发布与开源",
    "本地推理与量化",
    "AI 智能体",
    "AI 基准与评估",
    "AI 安全与政策",
    "行业动态与融资",
    "CUDA / GPU 基础设施",
    "安全与漏洞",
    "产品与工具更新",
    "研究与论文",
]

SYSTEM_PROMPT = """\
你是一个新闻分类助手。根据当天所有新闻条目的标签和标题，将它们分成若干主题板块。

参考分类（可按实际情况调整、合并、新增）：
{reference_topics}

规则：
1. 每个板块至少 2 条，内容过少的合并到相近板块或归入"综合"
2. 一条新闻只能属于一个板块
3. 板块名简洁（2-6 个汉字）
4. 返回 JSON 格式：{{"topics": [{{"name": "板块名", "item_indices": [0, 1, 3]}}]}}
   item_indices 是条目在输入列表中的从 0 开始的索引
5. 所有条目都必须被分配到某个板块，不能遗漏"""

USER_TEMPLATE = """\
以下是 {group_name} 的 {count} 条新闻（索引: 标题 [标签]）：

{items_text}

请按主题分板块，返回 JSON。"""


class TopicClassifier:
    """Classifies items into sub-topics using one AI call per group."""

    def __init__(self, ai_client):
        self.ai_client = ai_client

    async def classify_group(
        self,
        group_name: str,
        items: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Classify items into sub-topics.

        Args:
            group_name: Display name of the group (e.g. "头条速递")
            items: List of item dicts, each must have 'title' and 'tags' keys

        Returns:
            List of topic dicts: [{"name": str, "item_indices": [int, ...]}]
            Falls back to a single topic containing all items on failure.
        """
        if len(items) < 4:
            return [{"name": group_name, "item_indices": list(range(len(items)))}]

        items_lines = []
        for i, item in enumerate(items):
            tags_str = ", ".join(item.get("tags", [])[:5])
            items_lines.append(f"{i}: {item['title']} [{tags_str}]")
        items_text = "\n".join(items_lines)

        system = SYSTEM_PROMPT.format(
            reference_topics="\n".join(f"- {t}" for t in REFERENCE_TOPICS)
        )
        user = USER_TEMPLATE.format(
            group_name=group_name,
            count=len(items),
            items_text=items_text,
        )

        try:
            result = await self.ai_client.complete(
                system=system,
                user=user,
                temperature=0.1,
                max_tokens=2048,
            )
            topics = self._parse_response(result.text, len(items))
            logger.info("[%s] topic classification: %d topics", group_name,
                        len(topics))
            return topics
        except Exception as e:
            logger.warning("topic classification failed for %s: %s", group_name, e)
            return [{"name": group_name, "item_indices": list(range(len(items)))}]

    @staticmethod
    def _parse_response(text: str, total_items: int) -> List[Dict[str, Any]]:
        """Parse AI response JSON, validate indices, fix missing items."""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        data = json.loads(text)
        topics = data.get("topics", data if isinstance(data, list) else [])

        seen = set()
        result = []
        for topic in topics:
            name = topic.get("name", "未分类")
            indices = [i for i in topic.get("item_indices", [])
                       if isinstance(i, int) and 0 <= i < total_items]
            new_indices = [i for i in indices if i not in seen]
            if new_indices:
                seen.update(new_indices)
                result.append({"name": name, "item_indices": new_indices})

        missing = set(range(total_items)) - seen
        if missing:
            misc = [t for t in result if t["name"] == "综合"]
            if misc:
                misc[0]["item_indices"].extend(sorted(missing))
            else:
                result.append({"name": "综合", "item_indices": sorted(missing)})

        return result

    @staticmethod
    def apply_topics(
        items: List[Dict[str, Any]],
        topics: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Re-organize items list into topic-grouped structure for the template.

        Returns list of: {"name": str, "entries": [item_dicts]}
        """
        result = []
        for topic in topics:
            entries = [items[i] for i in topic["item_indices"]
                       if i < len(items)]
            if entries:
                result.append({"name": topic["name"], "entries": entries})
        return result
