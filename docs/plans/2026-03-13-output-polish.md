# 输出优化 — HTML 部署 + WxPusher 完整内容 + HTML 分组接入
> 深度: Lightweight

## 问题分析

三个独立的输出层优化点：

### 1. HTML 未部署到 GitHub Pages

`_copy_to_jekyll` 只复制 Markdown 到 `docs/_posts/`，HTML 报告 (`data/html/horizon-{date}.html`) 没有部署。
仓库已公开，GitHub Pages 已启用（`docs/` 目录，main 分支）。

### 2. WxPusher 推送内容过短

`BriefRenderer._get_summary()` 调用 `_first_sentence()` 将每条摘要截断为第一句话（最多 80 字符）。
用户需求：条数由 `top_n` 控制，但每条内容应完整展示。

### 3. HTML 日报缺少板块内分组

`TopicClassifier` 已存在且 Jinja2 模板已支持 `group.sub_topics`，但 `HtmlDetailRenderer.render()` 从未调用分类器。
模板中 `{% if group.sub_topics %}` 永远为 false，所有条目平铺显示。

## 修复方案

### 修复 1: HTML 部署

在 `orchestrator.py` 的 `_render_outputs` 中，生成 HTML 后将其复制到 `docs/reports/horizon-{date}.html`。

```python
# orchestrator.py — _render_outputs，html 生成成功后
self._copy_html_to_pages(today, html_content)
```

新增方法：
```python
def _copy_html_to_pages(self, date: str, html: str) -> None:
    reports_dir = Path("docs/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    dest = reports_dir / f"horizon-{date}.html"
    dest.write_text(html, encoding="utf-8")
```

### 修复 2: BriefRenderer 保留完整摘要

修改 `BriefRenderer._get_summary()`，直接返回完整摘要，不调用 `_first_sentence()`。

```python
@staticmethod
def _get_summary(item: ContentItem) -> str:
    meta = item.metadata
    return (
        meta.get("detailed_summary_zh")
        or meta.get("detailed_summary")
        or item.ai_summary
        or item.title
    )
```

### 修复 3: HTML 分组接入 TopicClassifier

在 `orchestrator._render_outputs` 中，渲染 HTML 之前调用 `TopicClassifier` 对每个有评分的 group 做子话题分类，把结果组织好传给 `HtmlDetailRenderer.render()`。

`HtmlDetailRenderer.render()` 的 groups 参数格式扩展为：
```python
{
    "name": "头条速递",
    "entries": [...],
    "sub_topics": [           # 新增：可选
        {"name": "模型发布", "entries": [...]},
        {"name": "AI 安全", "entries": [...]},
    ]
}
```

`HtmlDetailRenderer.render()` 内部修改：使用 `TopicClassifier.apply_topics()` 静态方法将分类结果映射到 entries 上。

### 涉及文件

| 文件 | 改动 |
|------|------|
| `src/orchestrator.py` | `_render_outputs` 中增加 HTML 复制 + TopicClassifier 调用 |
| `src/renderers/brief.py` | `_get_summary` 去掉 `_first_sentence` |
| `src/renderers/html_detail.py` | `render()` 接收 sub_topics 并传递给模板 |

### 验收标准

1. `uv run pytest -v` 全部通过
2. HTML 文件出现在 `docs/reports/` 目录
3. Brief 摘要每条显示完整内容
4. HTML 报告中有分组子话题标签

---

## 实现计划

### Phase 0: HTML 部署 + BriefRenderer 修复

**涉及文件：**
- 修改: `src/orchestrator.py`（`_render_outputs` 中 html 生成后增加复制到 `docs/reports/`）
- 修改: `src/renderers/brief.py`（`_get_summary` 去掉 `_first_sentence` 截断）
- 修改: `tests/test_brief_renderer.py`（更新因 `_first_sentence` 行为变更而失败的测试）

**关键改动：**

`src/orchestrator.py` — `_render_outputs` 方法中 html 块（约第 267-277 行），生成成功后新增：

```python
self._copy_html_to_pages(today, html_content)
```

新增辅助方法：

```python
def _copy_html_to_pages(self, date: str, html: str) -> None:
    try:
        reports_dir = Path("docs/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        dest = reports_dir / f"horizon-{date}.html"
        dest.write_text(html, encoding="utf-8")
        self.console.print(f"\N{GLOBE WITH MERIDIANS} Copied HTML report to GitHub Pages: {dest}\n")
    except Exception as e:
        self.console.print(f"[yellow]Failed to copy HTML to docs/: {e}[/yellow]\n")
```

`src/renderers/brief.py` — `_get_summary` 修改：

```python
@staticmethod
def _get_summary(item: ContentItem) -> str:
    meta = item.metadata
    return (
        meta.get("detailed_summary_zh")
        or meta.get("detailed_summary")
        or item.ai_summary
        or item.title
    )
```

`tests/test_brief_renderer.py` — 更新受影响的测试：
- `test_summary_fallback_chain`：断言改为验证完整摘要（不再截断为第一句）
- `test_summary_no_punctuation_truncation`：断言改为验证完整文本（不再出现 `…`）

**验证：**
在项目根目录执行 `uv run pytest tests/test_brief_renderer.py -v`，预期全部通过。

### Phase 1: HTML 分组接入 TopicClassifier

**涉及文件：**
- 修改: `src/orchestrator.py`（`_render_outputs` 中 html 渲染前调用 TopicClassifier）
- 修改: `src/renderers/html_detail.py`（`render()` 中为 scored group 调用 `TopicClassifier.apply_topics()`）

**关键改动：**

`src/orchestrator.py` — `_render_outputs` 方法中 html 块，渲染之前对每个 scored group 调用分类器：

```python
from .renderers.topic_classifier import TopicClassifier

if self.config.output.html.enabled:
    try:
        from .renderers.html_detail import HtmlDetailRenderer
        ai_client_classify = create_ai_client(self.config.ai)
        classifier = TopicClassifier(ai_client_classify)
        classified_groups = await self._classify_for_html(
            classifier, grouped_for_summary
        )
        html_renderer = HtmlDetailRenderer()
        html_content = html_renderer.render(
            classified_groups, today, total_fetched
        )
        # ... 保存 + 复制
```

orchestrator 中新增辅助方法，为每个有评分的 group 调用 classifier：

```python
async def _classify_for_html(
    self,
    classifier,
    grouped: Dict[str, List[ContentItem]],
) -> Dict[str, List[Dict]]:
    """对每个有评分条目的 group 调用 TopicClassifier.classify_group()。
    
    返回 {group_name: [{"name": "板块名", "item_indices": [0, 1, ...]}]}
    仅对有 ai_score 的 group 分类；无评分的 group（如"关注动态"）跳过。
    """
    result: Dict[str, List[Dict]] = {}
    for name, items in grouped.items():
        has_scores = any(it.ai_score is not None for it in items)
        if not has_scores or len(items) < 4:
            continue
        item_dicts = [
            {"title": it.title, "tags": it.ai_tags or []}
            for it in items
        ]
        topics = await classifier.classify_group(name, item_dicts)
        result[name] = topics
    return result
```

`src/renderers/html_detail.py` — `render()` 方法修改：

新增可选参数 `sub_topics_map`（来自 orchestrator 的分类结果），构建模板数据时为每个 group 附加 `sub_topics` 字段：

```python
def render(
    self,
    grouped_items: Dict[str, List[ContentItem]],
    date: str,
    total_fetched: int,
    sub_topics_map: Optional[Dict[str, List[Dict]]] = None,
) -> str:
    from .topic_classifier import TopicClassifier
    groups = []
    for name, items in grouped_items.items():
        if not items:
            continue
        entries = [self._prepare_item(item) for item in items]
        group_data = {"name": name, "entries": entries}
        if sub_topics_map and name in sub_topics_map:
            group_data["sub_topics"] = TopicClassifier.apply_topics(
                entries, sub_topics_map[name]
            )
        groups.append(group_data)
    return self.template.render(
        date=date, total_fetched=total_fetched,
        total_selected=sum(len(items) for items in grouped_items.values()),
        groups=groups,
    )
```

**验证：**
在项目根目录执行 `uv run pytest -v`，预期全部通过。

## 状态
- [x] Phase 0: HTML 部署 + BriefRenderer 修复
- [x] Phase 1: HTML 分组接入 TopicClassifier
- [x] 验证与审阅
