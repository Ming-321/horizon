# Phase 1 核心架构升级 — 六项后续优化

> 深度: Deep

## 背景

Phase 1 核心架构升级已完成并通过端到端验证。运行结果暴露了以下可优化点：

1. 关注动态中同一仓库产生多条碎片更新（如 Superpowers 12 条 commit）
2. GitHub 热榜数据源仅 9 条原始条目，最终只保留 4 条
3. bypass 组（关注动态）虽然跳过了 scoring，但仍执行完整 enrichment（13 条 × 2 次 AI 调用 + web 搜索）
4. 所有 scoring prompt_file 均为 null，全部回退到通用全局默认
5. 不同 group 使用相同 enrichment prompt，但实际需求差异很大
6. 没有统一日志系统，关键阶段耗时和诊断信息无法持久化

## 参考

| 文件 | 说明 |
|------|------|
| `src/orchestrator.py` | 核心管道，涉及优化 1/3/5/6 |
| `src/scrapers/rss.py` | RSS 抓取，commit 聚合的数据来源 |
| `src/ai/enricher.py` | enrichment 流程，涉及优化 5/6 |
| `src/ai/analyzer.py` | scoring 流程，涉及优化 6 |
| `src/ai/prompts.py` | prompt 分层 fallback 实现 |
| `data/prompts/scoring_default.txt` | 当前唯一 prompt 文件 |
| `data/config.json` | 配置文件，涉及优化 1/2/3/4/5 |
| `src/models.py` | 数据模型，涉及优化 5 |
| `src/main.py` | CLI 入口，涉及优化 6 |

## 设计决策

### 优化 1：同仓库多条更新合并

**决策**：在 orchestrator.run() 中 group routing 之前新增 repo 级聚合步骤。

**聚合条件**：
- 仅对 `category == "github-updates"` 的 RSS items 执行
- 按 `metadata["feed_name"]` 分组（每个 commits.atom feed 在配置中有独立 name）
- 同一 feed_name 的多条 item 合并为一条

**合并规则**：
- 标题：`{feed_name}: {N} updates`（附上最新 1-2 条 commit 的摘要）
- content：所有 commit message 按时间倒序拼接
- published_at：取最新时间
- URL：取最新条目的 URL
- category、metadata.feed_name 保持不变

**影响文件**：`src/orchestrator.py`（新增 `_aggregate_repo_updates` 方法 + run() 调用）

**不影响**：其他 category 的 RSS 内容不受影响；GitHub API releases 不受影响。

### 优化 2：扩展 GitHub 热榜数据源

**决策**：在 config.json 中新增 3 个 trending RSS 源。

**新增源**：

| Name | URL | Category |
|------|-----|----------|
| GitHub Trending - Python | `https://mshibanami.github.io/GitHubTrendingRSS/daily/python.xml` | github-trending |
| GitHub Trending - TypeScript | `https://mshibanami.github.io/GitHubTrendingRSS/daily/typescript.xml` | github-trending |
| GitHub Trending - CUDA | `https://mshibanami.github.io/GitHubTrendingRSS/daily/cuda.xml` | github-trending |

去重后预计覆盖 25-30 条原始热榜，评分后约 10-15 条。

**影响文件**：`data/config.json`、`data/config.example.json`

### 优化 3：bypass 组控制 enrichment 模式

**决策**：已合并到优化 5 的 `enrichment_mode` 字段中。关注动态组配置为 `"enrichment_mode": "summary_only"`。

参见优化 5 的详细设计。

### 优化 4：定制化 scoring prompt

**决策**：为头条速递和 GitHub 热榜各写一个完整独立的 system prompt，保留 0-10 分框架和 JSON 输出格式，但从零设计评分标准。

**头条速递 (`scoring_headlines.txt`)**：
- 保留 0-10 分档位结构
- 评分维度：
  - AI 应用/工具发布 → 高权重
  - ML 研究突破（新模型/新方法） → 高权重
  - 安全/隐私重大事件 → 中高权重
  - 深度技术分析文章 → 中高权重
  - 通用科技新闻 → 正常权重
  - 纯硬件发布（非 AI）→ 正常权重
  - 翻炒旧闻 → 低权重
**GitHub 热榜 (`scoring_github_trending.txt`)**：
- 重新定义分档位（适配开源项目）
- 评分维度：
  - AI/ML 工具、框架、模型库 → 高权重
  - 有可运行代码 + 文档完善 → 加分
  - 解决实际问题的新颖方案 → 加分
  - 纯 awesome-list/资源汇编 → 大幅降权
  - 只有 README 无实质代码 → 大幅降权
  - 教程/demo 类 → 降权

**关注动态**：bypass，无需 prompt。

**全局默认 (`scoring_default.txt`)**：保持不变，作为未配置 prompt 的 fallback。

**影响文件**：新增 `data/prompts/scoring_headlines.txt`、`data/prompts/scoring_github_trending.txt`；修改 `data/config.json` 中各组的 `scoring.prompt_file`

### 优化 5：定制化 enrichment prompt

**决策**：给 enrichment 引入 group 级别的 prompt 选择和模式控制。

**模式定义**：
- `full`（默认）：完整流程——concept extraction → web search → AI enrichment
- `summary_only`：仅 1 次 AI 调用生成变更摘要，跳过 concept extraction 和 web search
- `none`：完全跳过 enrichment

**各 Group 配置**：

| Group | enrichment_mode | 说明 |
|-------|----------------|------|
| 头条速递 | full | 完整新闻分析（当前行为） |
| 关注动态 | summary_only | 只对合并后的 commit 列表生成变更摘要 |
| GitHub 热榜 | full | 完整分析，但使用项目推荐模式的 prompt |

**模型修改**：
- `GroupConfig` 增加 `enrichment_mode: str = "full"`（替代原来的 `enrichment_enabled: bool`）

**Enricher 改动**：
- `ContentEnricher.enrich_batch()` 接受可选的 `system_prompt` 参数
- 新增 `summarize_only()` 方法：只调用 1 次 AI 生成摘要，不做 web 搜索

**Orchestrator 改动**：
- 按 group 的 `enrichment_mode` 分别处理：
  - `full`：调用 `enrich_batch(items, system_prompt=...)`
  - `summary_only`：调用 `enricher.summarize_only(items, system_prompt=...)`
  - `none`：跳过

**新增 prompt 文件**：

| 文件 | 用途 |
|------|------|
| `enrichment_headlines.txt` | 头条速递 enrichment（当前行为的外部化，可选——也可保持硬编码） |
| `enrichment_github_trending.txt` | GitHub 热榜 enrichment（项目推荐模式：侧重功能介绍、使用场景、同类对比） |
| `enrichment_watched_summary.txt` | 关注动态 summary_only 模式 prompt（变更摘要：列出关键改动点，精炼描述） |

**影响文件**：`src/models.py`、`src/ai/enricher.py`、`src/orchestrator.py`、`data/config.json`、`data/config.example.json`、新增 2-3 个 prompt 文件

### 优化 6：统一日志系统

**决策**：在 CLI 入口添加全局日志配置，为关键模块补充 info 级日志，迁移 print() 到 logger。

**日志配置**：
- 入口：`src/main.py`
- 日志文件：`data/logs/horizon-YYYY-MM-DD.log`
- 格式：`{时间(精确到分钟)} {模块} {级别} {消息}`，即 `%Y-%m-%d %H:%M %(name)s %(levelname)s %(message)s`
- 文件级别：INFO
- 控制台：保持 `rich.console.print` 不变（用户可见进度）

**关键节点日志**（仅写入文件）：
- orchestrator.run()：每个阶段开始/结束 + 处理条数 + 耗时
- enricher.enrich_batch()：开始/完成 + 条数 + 耗时
- analyzer.analyze_batch()：开始/完成 + 条数 + 耗时

**print() 迁移**：
- `src/ai/enricher.py:53` `print(f"Error enriching...")` → `logger.error(...)`
- `src/ai/analyzer.py:107` `print(f"Error analyzing...")` → `logger.error(...)`
- `src/ai/analyzer.py:197` `print(f"Warning: could not parse...")` → `logger.warning(...)`
- `src/ai/enricher.py:231` `print(f"Warning: could not parse...")` → `logger.warning(...)`

**影响文件**：`src/main.py`、`src/orchestrator.py`、`src/ai/enricher.py`、`src/ai/analyzer.py`

---

## 实现计划

### Phase 0: 数据模型 + RSS 源扩展

**涉及文件：**
- 修改: `src/models.py`（GroupConfig 新增 `enrichment_mode` 和 `enrichment_prompt_file` 字段）
- 修改: `data/config.json`（新增 3 条 trending RSS 源 + groups 增加 `enrichment_mode` / `enrichment_prompt_file`）
- 修改: `data/config.example.json`（同步）

**关键改动：**

`src/models.py` — GroupConfig 新增：

```python
class GroupConfig(BaseModel):
    id: str
    name: str
    default: bool = False
    categories: List[str] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    summary: SummaryGroupConfig = Field(default_factory=SummaryGroupConfig)
    enrichment_mode: str = "full"  # "full" | "summary_only" | "none"
    enrichment_prompt_file: Optional[str] = None
```

`data/config.json` — groups 部分（注意：`scoring.prompt_file` 和 `enrichment_prompt_file` 均暂保持 null，在 Phase 2 创建 prompt 文件后再回填）：

```json
{
  "id": "headlines",
  "enrichment_mode": "full",
  "enrichment_prompt_file": null,
  "scoring": { "enabled": true, "prompt_file": null, "threshold": 7.0 }
},
{
  "id": "watched",
  "enrichment_mode": "summary_only",
  "enrichment_prompt_file": null,
  "scoring": { "enabled": false }
},
{
  "id": "github_trending",
  "enrichment_mode": "full",
  "enrichment_prompt_file": null,
  "scoring": { "enabled": true, "prompt_file": null, "threshold": 7.0 }
}
```

`data/config.json` — rss 部分新增 3 条：

```json
{ "name": "GitHub Trending - Python", "url": "https://mshibanami.github.io/GitHubTrendingRSS/daily/python.xml", "enabled": true, "category": "github-trending" },
{ "name": "GitHub Trending - TypeScript", "url": "https://mshibanami.github.io/GitHubTrendingRSS/daily/typescript.xml", "enabled": true, "category": "github-trending" },
{ "name": "GitHub Trending - CUDA", "url": "https://mshibanami.github.io/GitHubTrendingRSS/daily/cuda.xml", "enabled": true, "category": "github-trending" }
```

**验证：**
在项目根目录执行 `uv run pytest tests/test_models_groups.py -v`，预期全部通过。本 Phase 需在 `tests/test_models_groups.py` 中新增断言：
- `GroupConfig(id="x", name="X")` 默认 `enrichment_mode == "full"` 且 `enrichment_prompt_file is None`
- `GroupConfig(id="x", name="X", enrichment_mode="summary_only", enrichment_prompt_file="foo.txt")` 可正确赋值
- `Config(**raw)` 读入带新字段的 config 后属性可访问且值正确

### Phase 1: 同仓库更新合并

**涉及文件：**
- 修改: `src/orchestrator.py`（新增 `_aggregate_repo_updates` 方法 + run() 调用）
- 新增: `tests/test_repo_aggregation.py`

**关键改动：**

`src/orchestrator.py` — 新增方法：

**过滤条件**：`item.category == "github-updates"` 且 `item.metadata.get("feed_name")` 非空。由于 `github-updates` category 仅由 RSS scraper 产生，无需额外判断 `source_type`。

**输出契约**：
- `title`: `"{feed_name}: {N} updates — {最新1-2条commit摘要}"`
- `content`: 按时间倒序，每行 `"- [HH:MM] {commit_title}"`（`published_at` 缺失时用 `??:??`）
- `published_at`: 取最新条目
- `url`: 取最新条目
- `metadata`: 继承最新条目，额外添加 `commit_count: int`

```python
@staticmethod
def _aggregate_repo_updates(items: List[ContentItem]) -> List[ContentItem]:
    """Merge multiple commits from the same repo (commits.atom) into a single item."""
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
        repo_items.sort(key=lambda x: x.published_at or datetime.min, reverse=True)
        newest = repo_items[0]
        top_summaries = [it.title.split(": ", 1)[-1] if ": " in it.title else it.title for it in repo_items[:2]]
        title = f"{feed_name}: {len(repo_items)} updates \u2014 {', '.join(top_summaries)}"
        content_lines = [f"- [{it.published_at.strftime('%H:%M') if it.published_at else '??:??'}] {it.title}" for it in repo_items]
        merged = ContentItem(
            id=newest.id, source_type=newest.source_type,
            title=title, url=newest.url,
            content="\n".join(content_lines),
            author=newest.author, published_at=newest.published_at,
            category=newest.category,
            metadata={**newest.metadata, "commit_count": len(repo_items)},
        )
        aggregated.append(merged)
    return aggregated
```

`run()` 中，在 `merged_items = self.merge_cross_source_duplicates(...)` 之后、`grouped = self._route_to_groups(...)` 之前插入：

```python
merged_items = self._aggregate_repo_updates(merged_items)
```

`tests/test_repo_aggregation.py`（新增）：
- 单 repo 多条合并：验证 title 格式、content 倒序、`published_at` 取最新、`metadata["commit_count"]`
- 单条不合并：保持原样
- 非 `github-updates` category 不受影响

**验证：**
在项目根目录执行 `uv run pytest tests/test_repo_aggregation.py -v`，预期全部通过。

### Phase 2: 定制化 scoring + enrichment prompt 文件

**涉及文件：**
- 新增: `data/prompts/scoring_headlines.txt`
- 新增: `data/prompts/scoring_github_trending.txt`
- 新增: `data/prompts/enrichment_github_trending.txt`
- 新增: `data/prompts/enrichment_watched_summary.txt`
- 修改: `data/config.json`（更新 groups 中的 `scoring.prompt_file` 和 `enrichment_prompt_file`）
- 修改: `data/config.example.json`（同步以上变更）

**关键改动：**

`scoring_headlines.txt` — 完整的头条速递评分标准。必须输出 JSON `{score, reason, summary, tags}`。
- 评分分档：9-10（必看）、7-8（重要）、5-6（一般）、3-4（低优先级）、1-2（不相关）
- 高权重（+2~3 分）：AI 应用/工具发布、ML 研究突破（新模型/新方法）、安全/隐私重大事件、深度技术分析文章
- 正常权重（基准分）：通用科技新闻、非 AI 硬件发布
- 低权重（-2~3 分）：翻炒旧闻、纯营销/PR 内容、非技术娱乐内容

`scoring_github_trending.txt` — 完整的 GitHub 热榜评分标准。必须输出 JSON `{score, reason, summary, tags}`。
- 评分分档（适配开源项目）：9-10（必须关注）、7-8（值得了解）、5-6（一般项目）、3-4（低价值）、1-2（无关）
- 高权重（+2~3 分）：AI/ML 工具框架、AI 模型库、有可运行代码 + 完善文档的实用项目、解决实际问题的新颖方案
- 正常权重（基准分）：通用开发工具、系统/基础设施项目
- 大幅降权（-3~4 分）：纯 awesome-list/资源汇编、只有 README 无实质代码、教程/demo 类项目

`enrichment_github_trending.txt` — 项目推荐模式 enrichment prompt：侧重功能介绍、使用场景、同类对比。输出 JSON 字段与 `CONTENT_ENRICHMENT_SYSTEM` 一致（`background`, `detailed_summary`, `detailed_summary_zh`, `key_points`）。

`enrichment_watched_summary.txt` — 变更摘要模式 prompt：列出关键改动点，生成中英双语摘要。输出 `{summary_en, summary_zh}`。

头条速递 enrichment：保持现有 `CONTENT_ENRICHMENT_SYSTEM` 硬编码（当 `enrichment_prompt_file` 为 null 时自动回退）。

创建完文件后，更新 `data/config.json` 和 `data/config.example.json` 中 groups：
- headlines: `"scoring": { "prompt_file": "scoring_headlines.txt" }`, `"enrichment_prompt_file": null`（保持）
- watched: `"enrichment_prompt_file": "enrichment_watched_summary.txt"`
- github_trending: `"scoring": { "prompt_file": "scoring_github_trending.txt" }`, `"enrichment_prompt_file": "enrichment_github_trending.txt"`

**验证：**
在项目根目录执行以下命令：

1. `uv run pytest tests/test_group_pipeline.py -v`（预期全部通过）
2. 全量 prompt 文件加载验证：

```bash
uv run python -c "
from src.ai.prompts import load_prompt
for f in ['scoring_headlines.txt', 'scoring_github_trending.txt', 'enrichment_github_trending.txt', 'enrichment_watched_summary.txt']:
    p = load_prompt(f)
    assert p, f'{f} not found or empty'
    print(f'{f}: OK ({len(p)} chars)')
"
```

3. 手动检查 `data/config.json` 和 `data/config.example.json` 的 `scoring.prompt_file` / `enrichment_prompt_file` 值与新建文件名完全一致

### Phase 3: Enrichment 模式分流

**涉及文件：**
- 修改: `src/ai/enricher.py`（新增 `summarize_batch` / `_summarize_item` 方法，`enrich_batch` / `_enrich_item` 接受可选 `enrichment_system_prompt`）
- 修改: `src/orchestrator.py`（按 group.enrichment_mode 分流 enrichment，使用 `group.enrichment_prompt_file` 加载 prompt）
- 修改: `src/ai/prompts.py`（新增 `load_enrichment_prompt(group: GroupConfig) -> Optional[str]` 帮助函数）
- 新增: `tests/test_enrichment_modes.py`

**关键改动：**

`src/ai/prompts.py` — 新增：

```python
def load_enrichment_prompt(group: GroupConfig) -> Optional[str]:
    """Load enrichment prompt file for a group. Returns None if not configured or file missing."""
    if group.enrichment_prompt_file:
        return load_prompt(group.enrichment_prompt_file)
    return None
```

`src/ai/enricher.py` — 新增 `summarize_batch` / `_summarize_item`：

```python
_DEFAULT_SUMMARY_PROMPT = "Summarize the following content changes. Output JSON: {\"summary_en\": \"...\", \"summary_zh\": \"...\"}"

async def summarize_batch(self, items: List[ContentItem], system_prompt: str = None) -> TokenUsage:
    """Light-weight enrichment: single AI call per item, no web search.
    Writes: metadata["detailed_summary_en"], metadata["detailed_summary_zh"], metadata["detailed_summary"].
    Does NOT write: concepts, search_results, background, community_discussion.
    """
    prompt = system_prompt or _DEFAULT_SUMMARY_PROMPT
    tracker = TokenUsageTracker()
    for item in items:
        usage = await self._summarize_item(item, system_prompt=prompt)
        tracker.track(usage)
    return tracker.total

async def _summarize_item(self, item: ContentItem, system_prompt: str = None) -> TokenUsage:
    """Single AI call: generate {summary_en, summary_zh} from item content."""
    # AI 调用，解析 JSON 响应
    # 写入: item.metadata["detailed_summary_en"] = response["summary_en"]
    # 写入: item.metadata["detailed_summary_zh"] = response["summary_zh"]
    # 写入: item.metadata["detailed_summary"] = response["summary_en"]  (兼容下游)
    # 不调用 _extract_concepts 和 web search
    ...
```

`enrich_batch` / `_enrich_item` 签名变更：

```python
async def enrich_batch(self, items: List[ContentItem], enrichment_system_prompt: str = None) -> TokenUsage:
    ...

async def _enrich_item(self, item: ContentItem, enrichment_system_prompt: str = None) -> TokenUsage:
    system = enrichment_system_prompt or CONTENT_ENRICHMENT_SYSTEM  # null 时回退到硬编码默认
    ...
```

**关键 fallback 逻辑**：当 `enrichment_system_prompt` 为 `None` 时（即 `enrichment_prompt_file` 未配置或文件缺失），`_enrich_item` 自动回退到硬编码的 `CONTENT_ENRICHMENT_SYSTEM`。`_summarize_item` 同理回退到 `_DEFAULT_SUMMARY_PROMPT`。

`src/orchestrator.py` — `run()` 中替换原有的 `all_important` 合并+单次 enrich 调用为按组分流：

```python
enricher = ContentEnricher(ai_client)
for name, bucket in grouped.items():
    mode = bucket.group.enrichment_mode
    if mode == "none":
        self.console.print(f"   [{name}] skipped enrichment")
        continue
    prompt = load_enrichment_prompt(bucket.group)
    if mode == "summary_only":
        usage = await enricher.summarize_batch(bucket.items, system_prompt=prompt)
    else:
        usage = await enricher.enrich_batch(bucket.items, enrichment_system_prompt=prompt)
    tracker.track(usage)
    self.console.print(f"   [{name}] enriched {len(bucket.items)} items (mode={mode})")
```

`tests/test_enrichment_modes.py`（新增）：
- **路由逻辑**：mock enricher，验证 `full` 调用 `enrich_batch`、`summary_only` 调用 `summarize_batch`、`none` 不调用任何 enrichment
- **prompt 选择**：验证传入的 `system_prompt` / `enrichment_system_prompt` 参数值与 config 一致；null 时验证 fallback 到默认
- **metadata 写入**：`summary_only` 模式下断言 `metadata["detailed_summary_en"]` / `metadata["detailed_summary_zh"]` / `metadata["detailed_summary"]` 已写入
- **隔离验证**：`summary_only` 模式下 mock `_extract_concepts` / web search 相关方法，断言它们**未被调用**

**验证：**
在项目根目录执行 `uv run pytest tests/test_enrichment_modes.py tests/test_group_pipeline.py -v`，预期全部通过。

### Phase 4: 统一日志系统

**涉及文件：**
- 修改: `src/main.py`（添加全局日志初始化）
- 修改: `src/orchestrator.py`（添加模块级 logger + 关键阶段耗时日志）
- 修改: `src/ai/enricher.py`（添加模块级 logger + print → logger）
- 修改: `src/ai/analyzer.py`（添加模块级 logger + print → logger）

**关键改动：**

`src/main.py` — 在 `load_dotenv()` 之后：

```python
import logging
log_dir = Path("data/logs")
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / f"horizon-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M",
    handlers=[logging.FileHandler(log_file, encoding="utf-8")],
)
```

`src/orchestrator.py` — 模块级 `logger = logging.getLogger(__name__)`。在 run() 的每个阶段记录 **start + end**，包含条数和耗时：

| 阶段 | 开始日志 | 结束日志 |
|------|---------|---------|
| fetch | `logger.info("fetch: starting...")` | `logger.info("fetch: completed %d items in %.1fs", count, elapsed)` |
| dedup | `logger.info("dedup: starting with %d items", count)` | `logger.info("dedup: %d → %d items in %.1fs", before, after, elapsed)` |
| repo_aggregation | `logger.info("repo_aggregation: starting with %d items", count)` | `logger.info("repo_aggregation: %d → %d items in %.1fs", before, after, elapsed)` |
| route | `logger.info("route: starting...")` | `logger.info("route: %d groups [%s]", n, group_summary)` |
| score (per group) | `logger.info("[%s] scoring: starting %d items", name, total)` | `logger.info("[%s] scoring: %d/%d above threshold in %.1fs", name, passed, total, elapsed)` |
| enrich (per group) | `logger.info("[%s] enrich: starting %d items (mode=%s)", name, count, mode)` | `logger.info("[%s] enrich: completed %d items in %.1fs", name, count, elapsed)` |
| summarize | `logger.info("summarize: starting for %s", lang)` | `logger.info("summarize: completed for %s in %.1fs", lang, elapsed)` |

`src/ai/enricher.py` — 模块级 `logger = logging.getLogger(__name__)`：
- batch-level start/end：
  - `enrich_batch`: `logger.info("enrich_batch: starting %d items", len(items))` / `logger.info("enrich_batch: completed %d items in %.1fs", len(items), elapsed)`
  - `summarize_batch`: `logger.info("summarize_batch: starting %d items", len(items))` / `logger.info("summarize_batch: completed %d items in %.1fs", len(items), elapsed)`
- 迁移 print 到 logger：
  - `print(f"Error enriching...")` → `logger.error("Error enriching item %s: %s", item.id, e)`
  - `print(f"Warning: could not parse...")` → `logger.warning("Could not parse enrichment response for %s", item.id)`

`src/ai/analyzer.py` — 模块级 `logger = logging.getLogger(__name__)`：
- batch-level start/end：
  - `analyze_batch`: `logger.info("analyze_batch: starting %d items", len(items))` / `logger.info("analyze_batch: completed %d items in %.1fs", len(items), elapsed)`
- 迁移 print 到 logger：
  - `print(f"Error analyzing...")` → `logger.error("Error analyzing item %s: %s", item.id, e)`
  - `print(f"Warning: could not parse...")` → `logger.warning("Could not parse analysis response for %s", item.id)`

**验证：**

1. 在项目根目录执行 `uv run pytest tests/ -v`，预期全部通过（logger 不影响测试行为）
2. 日志文件生成验证（单独执行一次 CLI smoke run 或在 Phase 5 集成测试中验证）：

```bash
# 验证日志文件已创建
ls -la data/logs/horizon-$(date +%Y-%m-%d).log

# 验证关键阶段日志存在（至少包含 fetch / dedup / route / enrich / summarize）
rg "fetch:|dedup:|route:|enrich:|summarize:" data/logs/horizon-$(date +%Y-%m-%d).log
```

### Phase 5: 验证与审阅

**涉及文件：** 无新增修改

**验证：**
1. 执行 `uv run pytest -v`，全部通过
2. sub-agent 代码审阅，审阅清单：
   - **config schema**：`GroupConfig` 新字段 `enrichment_mode` / `enrichment_prompt_file` 可正确解析
   - **RSS 源扩展**：新增 3 条 trending RSS 源已写入 `data/config.json`，category 为 `github-trending`
   - **repo aggregation**：输出格式符合契约（title 格式、content 倒序、metadata.commit_count）
   - **group prompt 路由**：各 group 的 `scoring.prompt_file` 和 `enrichment_prompt_file` 正确绑定到对应文件
   - **null fallback**：`enrichment_prompt_file` 为 null 时 `_enrich_item` 回退到 `CONTENT_ENRICHMENT_SYSTEM`；`_summarize_item` 回退到 `_DEFAULT_SUMMARY_PROMPT`
   - **summary_only 隔离**：跳过 `_extract_concepts` 和 web search，仅调用 1 次 AI
   - **scoring prompt 内容合规**：头条评分维度（AI/安全/深度技术分析）完整；热榜评分维度（AI 工具/awesome-list 降权）完整
   - **日志文件生成**：`data/logs/horizon-YYYY-MM-DD.log` 已创建，包含各阶段 start/end 日志
   - **print 迁移**：`enricher.py` / `analyzer.py` 中无残留 `print()` 调用
   - **config.example.json 同步**：与 `config.json` 的 group 配置一致

## 状态
- [x] Phase 0: 数据模型 + RSS 源扩展
- [x] Phase 1: 同仓库更新合并
- [x] Phase 2: 定制化 scoring + enrichment prompt 文件
- [x] Phase 3: Enrichment 模式分流
- [x] Phase 4: 统一日志系统
- [x] Phase 5: 验证与审阅
