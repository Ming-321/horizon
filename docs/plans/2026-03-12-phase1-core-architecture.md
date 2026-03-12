# 阶段一：核心架构升级
> 深度: Deep

## 背景

Horizon 当前采用扁平管道：所有 source → 统一 AI 评分 → 统一阈值过滤 → 统一摘要渲染。这带来以下问题：

1. **无法差异化处理**：关注的仓库更新（如 MemSearch、Superpowers commits）和通用新闻使用相同评分标准，低分内容被过滤掉
2. **Prompt 不可定制**：所有内容用同一个评分 prompt，无法针对不同类别调整评分标准
3. **摘要过短**：全局 prompt 难以控制不同类型内容的输出风格和长度
4. **无 API 用量感知**：每次运行消耗多少 token 无法量化

## 参考

- `docs/plans/roadmap.md` 阶段一部分 — 需求定义、配置结构设计、关键改动列表
- 当前 `src/orchestrator.py` — 扁平管道实现（fetch → analyze → filter → enrich → summarize）
- 当前 `src/ai/client.py` — `AIClient.complete()` 只返回 `str`，无 usage 信息
- 当前 `src/ai/analyzer.py` — `ContentAnalyzer` 使用硬编码 prompt（`prompts.py`）
- 当前 `src/ai/summarizer.py` — `DailySummarizer` 纯程序化渲染，无分组概念
- 当前 `data/config.json` — 无 `groups` 配置，`filtering.ai_score_threshold` 为全局唯一阈值
- 当前 `src/models.py` — `Config` 无分组模型，`ContentItem` 无 `category` 字段但 `RSSSourceConfig` 有

## 术语定义

| 术语 | 英文 | 说明 | 举例 |
|------|------|------|------|
| 信息源类型 | Source Type | 数据获取渠道的类型 | `rss`、`github`、`telegram`、`hackernews` |
| 信息源条目 | Source Entry | 最小配置单位，一个具体的数据来源 | `量子位`、`zaihuapd`、`vllm repo_releases` |
| 类别标签 | Category | 贴在 entry 上的路由标签，entry 抓到的所有 item 继承此标签 | `ai-news-zh`、`github-updates`、`telegram` |
| 分组 | Group | 报纸的"版面"，通过 categories 列表关联一组 category | `头条速递`、`关注动态`、`GitHub 热榜` |

**两个维度通过 Category 交叉连接**：

```
数据维度：Source Type → Source Entry → [每次抓取 N 条 ContentItem]
                             │
                          category（1 个标签）
                             │
路由维度：Group → categories 列表 → [匹配的 item 归入此 group]
```

## 设计决策

### 1. 分组（Groups）路由机制

**分组按阅读优先级设计**（而非按主题），模拟报纸分区体验：

| 分区 | 定位 | 评分 |
|------|------|------|
| 头条速递 | 经过 AI 评分筛选的高质量内容（不区分主题） | 启用 |
| 关注动态 | 主动关注的人/项目的更新（直通车） | 跳过 |
| GitHub 热榜 | 每日开源趋势 | 启用 |

**category 归类时机**：在 fetch 阶段由配置驱动完成，不需要 AI。各 scraper 创建 ContentItem 时根据 source entry 的 category 配置写入 `category` 字段。

**category 来源**：
- RSS：`RSSSourceConfig.category` 字段（已有）
- GitHub：按 source config type 映射（`user_events` → `github-activity`，`repo_releases` → `github-updates`）
- HackerNews：`HackerNewsConfig` 新增 `category` 字段，默认 `hackernews`
- Reddit：`RedditSubredditConfig` 新增 `category` 字段，默认 `reddit`
- Telegram：`TelegramChannelConfig` 新增 `category` 字段，默认 `telegram`

**路由规则**：item 的 category 匹配到 group 的 `categories` 列表则归入该组。未匹配任何 group 的 item 归入**默认组**（头条速递）。

**ContentItem 变更**：新增 `category: Optional[str]` 字段。

**当前 source entry 到 category 的完整映射**：

```
Group: 头条速递
  ├── category: tech-analysis    ← Ars Technica (RSS)
  ├── category: hackernews       ← HackerNews
  ├── category: ai-tools         ← Simon Willison (RSS)
  ├── category: ai-news-zh       ← 量子位 (RSS)
  ├── category: ai-open-source   ← Hugging Face Blog (RSS)
  ├── category: reddit-ml        ← r/MachineLearning (RSS)
  ├── category: reddit-llm       ← r/LocalLLaMA (RSS)
  └── category: telegram         ← zaihuapd (Telegram)

Group: 关注动态 (直通车)
  └── category: github-updates
        ├── MemSearch commits (RSS)
        └── Superpowers commits (RSS)

Group: GitHub 热榜
  └── category: github-trending  ← GitHub Trending Daily (RSS)
```

### 2. 自定义 Prompt（层级化降级）

**核心机制**：prompt 从硬编码改为支持从外部文件加载，采用 3 层降级查找链：

```
Source Entry 级别 prompt → Group 级别 prompt → 全局默认 prompt (prompts.py)
      (最优先)                                      (兜底)
```

- Source Entry 级别：极少使用，只有当某个具体 feed 非常特殊时才配
- Group 级别：更常用，给一个分区统一设定评分/摘要标准
- 全局默认：`prompts.py` 中的硬编码 prompt 作为兜底

**Prompt 外部化目录**：

```
data/prompts/
├── scoring_default.txt          ← 全局默认评分 prompt（从 prompts.py 迁移）
└── summary_default.txt          ← 全局默认摘要 prompt（未来扩展）
```

**加载逻辑（伪代码）**：

```python
def get_scoring_prompt(item, group):
    # 1. source entry 配了 scoring_prompt_file?
    if item.source_entry_config.scoring_prompt_file:
        return load_prompt(item.source_entry_config.scoring_prompt_file)
    # 2. group 配了 scoring.prompt_file?
    if group.scoring.prompt_file:
        return load_prompt(group.scoring.prompt_file)
    # 3. 全局默认
    return CONTENT_ANALYSIS_SYSTEM  # prompts.py
```

**配置字段**：
- 各 source entry config（`RSSSourceConfig` 等）新增可选 `scoring_prompt_file: Optional[str]`
- Group 的 `scoring` 配置中已有 `prompt_file`
- 路径相对于 `data/prompts/`

**说明**：当前 Summarizer 为纯程序化渲染（不调用 AI），`summary.prompt_file` 暂保留为未来扩展点。

### 3. 直通车（Bypass）

**机制**：group 配置 `scoring.enabled: false` 时，该组所有 item 跳过 AI 评分，直接进入后续流程。

**跳过评分的 item**：
- `ai_score` 保持 `None`（不参与分数排序）
- 直接通过，不受阈值过滤
- 仍然经过 enrich 步骤（获取背景知识）

### 4. API 用量统计

**实现方式**：`AIClient.complete()` 返回值从 `str` 改为 `CompletionResult(text, usage)`，其中 `usage` 包含 `prompt_tokens`、`completion_tokens`。

**统计汇总**：orchestrator 在运行结束时输出总 token 消耗。

**各 provider 的 usage 获取**：
- OpenAI/Doubao：`response.usage.prompt_tokens` / `completion_tokens`
- Anthropic：`message.usage.input_tokens` / `output_tokens`
- Gemini：`response.usage_metadata.prompt_token_count` / `candidates_token_count`

### 5. Orchestrator 管道改造

```
fetch all sources (各 scraper 写入 category)
    ↓
merge cross-source duplicates (不变)
    ↓
route items to groups (按 category 匹配)
    ↓
for each group:
    ├── scoring.enabled=true → AI 评分 (用层级化 prompt) → 按 group.threshold 过滤
    └── scoring.enabled=false → 直接通过（直通车）
    ↓
merge topic duplicates (在所有组的已过滤结果上)
    ↓
enrich (不变)
    ↓
generate summary (按 group 分区渲染)
```

### 6. Summarizer 分区渲染

`DailySummarizer.generate_summary()` 接收按 group 分区的 items，每个 group 渲染为独立的 section（带 group name 作为标题）。

### 7. 配置结构

```json
{
  "groups": [
    {
      "id": "headlines",
      "name": "头条速递",
      "default": true,
      "categories": ["tech-analysis", "hackernews", "ai-tools", "ai-news-zh", "ai-open-source", "reddit-ml", "reddit-llm", "reddit", "telegram", "github-activity", "linux-kernel"],
      "scoring": {
        "enabled": true,
        "prompt_file": null,
        "threshold": 7.0
      },
      "summary": {
        "prompt_file": null
      }
    },
    {
      "id": "watched",
      "name": "关注动态",
      "categories": ["github-updates"],
      "scoring": {
        "enabled": false
      },
      "summary": {
        "prompt_file": null
      }
    },
    {
      "id": "github_trending",
      "name": "GitHub 热榜",
      "categories": ["github-trending"],
      "scoring": {
        "enabled": true,
        "prompt_file": null,
        "threshold": 7.0
      },
      "summary": {
        "prompt_file": null
      }
    }
  ]
}
```

**向后兼容**：如果 config 中无 `groups` 字段，所有 item 使用现有的全局 `filtering.ai_score_threshold` 和默认 prompt，行为与当前完全一致。

### 8. 涉及的文件和关键接口变更

| 文件 | 改动 |
|------|------|
| `src/models.py` | 新增 `GroupConfig`、`ScoringConfig`、`SummaryGroupConfig` 模型；`ContentItem` 新增 `category` 字段；`Config` 新增 `groups` 字段；各 source entry config 新增 `category`（部分已有）和 `scoring_prompt_file` 可选字段 |
| `data/config.json` | 新增 `groups` 配置；各 source entry 补充 `category` 字段（HN、Telegram 等当前缺失的） |
| `src/ai/client.py` | `complete()` 返回 `CompletionResult(text, usage)` 替代 `str`；新增 `UsageStats`、`TokenUsageTracker` |
| `src/ai/analyzer.py` | `ContentAnalyzer` 接受外部 prompt 参数（按层级化降级查找） |
| `src/ai/summarizer.py` | `generate_summary()` 接受按 group 分区的 items，分区渲染 |
| `src/ai/enricher.py` | 适配 `CompletionResult` 返回值 |
| `src/ai/prompts.py` | 默认 prompt 仍保留作为 fallback，新增 prompt 文件加载工具函数 |
| `src/orchestrator.py` | 管道改为 group-aware：路由 → 按组评分/直通车 → 合并 → enrich → 按组聚合摘要；运行结束输出 token 消耗汇总 |
| `src/scrapers/hackernews.py`, `telegram.py`, `reddit.py`, `github.py`, `rss.py` | 各 scraper 在创建 `ContentItem` 时写入 `category` 字段 |
| `data/prompts/` | 新增目录，存放外部化的 prompt 文件 |

---

## 实现计划

### Phase 0: 数据模型与配置基础

**涉及文件：**
- 修改: `src/models.py`（新增 Group/Scoring 配置模型；ContentItem 新增 category；Config 新增 groups；各 source entry config 新增 category 和 scoring_prompt_file）
- 修改: `data/config.json`（新增 groups 配置；各 source entry 补充 category）
- 修改: `data/config.example.json`（同步更新示例配置）
- 新增: `tests/test_models_groups.py`（模型和配置加载测试）

**关键改动：**

`src/models.py` 新增模型：
```python
class ScoringConfig(BaseModel):
    enabled: bool = True
    prompt_file: Optional[str] = None
    threshold: float = 7.0

class SummaryGroupConfig(BaseModel):
    prompt_file: Optional[str] = None

class GroupConfig(BaseModel):
    id: str
    name: str
    default: bool = False
    categories: List[str] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    summary: SummaryGroupConfig = Field(default_factory=SummaryGroupConfig)
```

`ContentItem` 新增字段：
```python
category: Optional[str] = None
```

`Config` 新增字段：
```python
groups: List[GroupConfig] = Field(default_factory=list)
```

各 source entry config 新增可选字段：
- `HackerNewsConfig`：`category: str = "hackernews"`，`scoring_prompt_file: Optional[str] = None`
- `TelegramChannelConfig`：`category: str = "telegram"`，`scoring_prompt_file: Optional[str] = None`
- `RedditSubredditConfig`：`category: str = "reddit"`，`scoring_prompt_file: Optional[str] = None`
- `RedditUserConfig`：`category: str = "reddit"`，`scoring_prompt_file: Optional[str] = None`
- `GitHubSourceConfig`：`category: Optional[str] = None`，`scoring_prompt_file: Optional[str] = None`
- `RSSSourceConfig`：已有 `category`，新增 `scoring_prompt_file: Optional[str] = None`

`data/config.json` 新增 `groups` 配置块（3 个分组），并给 `hackernews` 和 `telegram.channels[0]` 补充 `category` 字段。

**验证：**
在项目根目录执行 `uv run pytest tests/test_models_groups.py -v`，测试内容：
- `data/config.json` 和 `data/config.example.json` 均可被 `Config` 成功解析
- `GroupConfig` 默认值正确（scoring.enabled=True, threshold=7.0）
- `ContentItem` 的 `category` 字段默认为 None
- 无 `groups` 字段的旧配置仍可正常加载（向后兼容）

再执行 `uv run pytest -v`，预期已有 25 个测试全部通过。

### Phase 1: AI Client 返回 usage 信息

**涉及文件：**
- 修改: `src/ai/client.py`（complete() 返回 CompletionResult；各 provider 提取 usage；新增 TokenUsageTracker）
- 修改: `src/ai/analyzer.py`（适配 CompletionResult 返回值）
- 修改: `src/ai/enricher.py`（适配 CompletionResult 返回值）
- 新增: `tests/test_ai_client.py`（CompletionResult 和 usage 提取测试）

**关键改动：**

`src/ai/client.py` 新增数据类：
```python
@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

@dataclass
class CompletionResult:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)

class TokenUsageTracker:
    """跨多次 AI 调用的 token 累加器。"""
    def __init__(self):
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.call_count = 0

    def track(self, usage: TokenUsage):
        self.total_prompt_tokens += usage.prompt_tokens
        self.total_completion_tokens += usage.completion_tokens
        self.call_count += 1

    def summary(self) -> str:
        total = self.total_prompt_tokens + self.total_completion_tokens
        return f"Token usage: {total:,} total ({self.total_prompt_tokens:,} prompt + {self.total_completion_tokens:,} completion) across {self.call_count} calls"
```

修改 `AIClient` 抽象接口：
```python
class AIClient(ABC):
    @abstractmethod
    async def complete(self, system, user, temperature=0.3, max_tokens=4096) -> CompletionResult:
        ...
```

各 provider 的 `complete()` 方法从 `return text` 改为 `return CompletionResult(text=text, usage=TokenUsage(...))`：
- `OpenAIClient`：`response.usage.prompt_tokens` / `response.usage.completion_tokens`
- `AnthropicClient`：`message.usage.input_tokens` / `message.usage.output_tokens`
- `GeminiClient`：`response.usage_metadata.prompt_token_count` / `response.usage_metadata.candidates_token_count`

`src/ai/analyzer.py` 和 `src/ai/enricher.py` 中所有 `await self.client.complete(...)` 调用处：
```python
# 改前
response = await self.client.complete(system=..., user=...)
result = self._parse_json_response(response)
# 改后
completion = await self.client.complete(system=..., user=...)
result = self._parse_json_response(completion.text)
```

`analyzer.analyze_batch()` 和 `enricher.enrich_batch()` 返回累计 `TokenUsage`，供 orchestrator 汇总。

**验证：**
在项目根目录执行 `uv run pytest tests/test_ai_client.py -v`，测试内容：
- Mock 各 provider 的 response，验证 `CompletionResult.usage` 映射正确
- `TokenUsageTracker.track()` 和 `summary()` 计算正确
- analyzer/enricher 的 mock 调用返回 `completion.text` 正确

再执行 `uv run pytest -v`，预期全部通过。

### Phase 2: Scraper category 注入

**涉及文件：**
- 修改: `src/scrapers/hackernews.py`（创建 ContentItem 时写入 category）
- 修改: `src/scrapers/telegram.py`（创建 ContentItem 时写入 category）
- 修改: `src/scrapers/reddit.py`（创建 ContentItem 时写入 category）
- 修改: `src/scrapers/github.py`（创建 ContentItem 时写入 category）
- 修改: `src/scrapers/rss.py`（从 metadata 提升到 ContentItem.category 顶级字段）
- 新增: `tests/test_scraper_category.py`（各 scraper 的 category 注入测试）

**关键改动：**

各 scraper 在创建 ContentItem 时设置 `category` 字段：
- `hackernews.py`：保存 config 引用 `self.hn_config = config`，写入 `category=self.hn_config.category`（默认 `"hackernews"`）
- `telegram.py`：`category=cfg.category`（从 TelegramChannelConfig 读取，默认 `"telegram"`）
- `reddit.py`：`category=sub_cfg.category`（从 RedditSubredditConfig 读取，默认 `"reddit"`）；RedditUserConfig 同理
- `github.py`：`category=source.category or ("github-updates" if source.type == "repo_releases" else "github-activity")`
- `rss.py`：`category=source.category`（从 metadata 提升到顶级字段，同时保留 metadata 中的 category 以兼容现有逻辑）

**验证：**
在项目根目录执行 `uv run pytest tests/test_scraper_category.py -v`，测试内容：
- 每个 scraper 创建的 ContentItem 的 category 字段值正确
- 默认 category 值正确（HN → "hackernews"、Telegram → "telegram"、Reddit → "reddit"）
- RSS 的 category 仍能从 source config 正确读取

再执行 `uv run pytest -v`，预期全部通过。

### Phase 3: 管道改造（路由 + 评分 + Prompt 外部化 + 分区渲染 + Token 统计）

**涉及文件：**
- 修改: `src/ai/prompts.py`（新增 `load_prompt()`、`get_scoring_prompt()` 工具函数）
- 修改: `src/ai/analyzer.py`（接受 item 级别的 prompt resolver）
- 修改: `src/ai/summarizer.py`（按 group 分区渲染）
- 修改: `src/orchestrator.py`（group-aware 管道全流程）
- 新增: `data/prompts/scoring_default.txt`（全局默认评分 prompt）
- 新增: `tests/test_group_pipeline.py`（管道核心行为测试）

**关键改动：**

**Prompt 外部化** — `src/ai/prompts.py`：
```python
from pathlib import Path

_PROMPTS_DIR = Path("data/prompts")

def load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8").strip()

def get_scoring_prompt(
    source_prompt_file: Optional[str],
    group_prompt_file: Optional[str],
) -> str:
    for f in (source_prompt_file, group_prompt_file):
        if f:
            return load_prompt(f)
    return CONTENT_ANALYSIS_SYSTEM
```

`data/prompts/scoring_default.txt` 内容即 `CONTENT_ANALYSIS_SYSTEM` 的文本（注：`summary_default.txt` 暂不创建，当前 summarizer 为纯程序化渲染不需要）。

**Analyzer item 级 prompt** — `src/ai/analyzer.py`：
```python
class ContentAnalyzer:
    def __init__(self, ai_client: AIClient):
        self.client = ai_client

    async def analyze_batch(
        self, items, batch_size=10,
        prompt_resolver: Optional[Callable[[ContentItem], str]] = None,
    ) -> Tuple[List[ContentItem], TokenUsage]:
        """prompt_resolver: 给定 item 返回该 item 应使用的 system prompt。
        None 时使用全局默认。"""
        ...

    async def _analyze_item(self, item, system_prompt=None):
        system_prompt = system_prompt or CONTENT_ANALYSIS_SYSTEM
        completion = await self.client.complete(system=system_prompt, user=user_prompt)
        ...
```

orchestrator 构建 prompt_resolver 并传入 analyzer：
```python
def _build_prompt_resolver(self, group: GroupConfig) -> Callable:
    source_prompt_map = self._build_source_prompt_map()
    def resolver(item: ContentItem) -> str:
        return get_scoring_prompt(
            source_prompt_file=source_prompt_map.get(item.category),
            group_prompt_file=group.scoring.prompt_file,
        )
    return resolver
```

**Summarizer 分区渲染** — `src/ai/summarizer.py`：

签名变更为接收 `OrderedDict[str, List[ContentItem]]`（group_name → items），每个 group 渲染为独立 section。

```python
async def generate_summary(
    self,
    grouped_items: Dict[str, List[ContentItem]],
    date: str,
    total_fetched: int,
    language: str = "en",
) -> str:
    """grouped_items: {group_name: [items]}，按 group 顺序渲染。"""
    ...
```

渲染逻辑：
- 遍历 grouped_items，每个 group 渲染为 `## {group_name}` section
- 各 section 内部保持原有的 item 渲染逻辑
- TOC 按 group 分层展示（`### {group_name}` + 条目列表）

**Orchestrator group-aware 管道** — `src/orchestrator.py`：

中间数据结构：`Dict[str, GroupBucket]`，其中：
```python
@dataclass
class GroupBucket:
    group: GroupConfig
    items: List[ContentItem] = field(default_factory=list)
```

新增 `_route_to_groups()` 方法，含向后兼容分支：
```python
def _route_to_groups(self, items):
    if not self.config.groups:
        # 无 groups 配置 → 构造单个默认组，使用全局 filtering 配置
        default = GroupConfig(
            id="_default", name="Daily",
            scoring=ScoringConfig(threshold=self.config.filtering.ai_score_threshold),
        )
        return {"Daily": GroupBucket(group=default, items=list(items))}

    # 有 groups 配置 → 按 category 路由
    ...
    # 未匹配的 item 归入 default=true 的 group
```

`run()` 改造为 group-aware，最终输出 `Dict[str, List[ContentItem]]` 传给 summarizer：
```python
# 按组评分/直通车
for name, bucket in grouped.items():
    if bucket.group.scoring.enabled:
        resolver = self._build_prompt_resolver(bucket.group)
        analyzed, usage = await analyzer.analyze_batch(bucket.items, prompt_resolver=resolver)
        tracker.track(usage)
        bucket.items = [i for i in analyzed if i.ai_score and i.ai_score >= bucket.group.scoring.threshold]
    # else: 直通车，不评分

# topic dedup（在每个 bucket 内独立进行，保持 group 归属）
for name, bucket in grouped.items():
    if bucket.group.scoring.enabled:
        bucket.items = self.merge_topic_duplicates(bucket.items)

# flatten for enrich
all_items = [i for b in grouped.values() for i in b.items]
await self._enrich_important_items(all_items)  # usage 同样 track

# 按组传给 summarizer
grouped_for_summary = {name: bucket.items for name, bucket in grouped.items() if bucket.items}
summary = await summarizer.generate_summary(grouped_for_summary, ...)

# 运行结束输出 token 统计
self.console.print(f"📊 {tracker.summary()}")
```

**验证：**
在项目根目录执行 `uv run pytest tests/test_group_pipeline.py -v`，测试内容：
- `_route_to_groups()`：正确路由、默认组回退、未匹配 category 处理
- 向后兼容：无 `groups` 配置时退回全局 `filtering.ai_score_threshold` 扁平管道
- `scoring.enabled=false` 直通车：item 跳过评分，`ai_score` 保持 None
- `get_scoring_prompt()` 三层降级：source entry → group → 全局默认
- grouped summary：输出包含 group name 作为 section 标题
- `TokenUsageTracker`：运行结束输出 token 消耗

再执行 `uv run pytest -v`，预期全部通过。

### Phase 4: MCP 适配与集成回归

**涉及文件：**
- 修改: `src/mcp/service.py`（适配 CompletionResult 返回值、grouped summary 接口）
- 修改: `tests/test_mcp_service_smoke.py`（补充 group-aware 场景的 smoke test）

**关键改动：**

`src/mcp/service.py` 中直接调用 `ContentAnalyzer.analyze_batch()` 和 `DailySummarizer.generate_summary()` 的地方需适配新签名：
- `analyze_batch()` 返回 `Tuple[List[ContentItem], TokenUsage]` → 适配解包
- `generate_summary()` 接受 `Dict[str, List[ContentItem]]` → MCP 场景下可包装为单组传入（保持 MCP 的简单使用场景不变）

适配策略：MCP service 作为集成入口，当用户通过 MCP 调用时，如果不需要 group 功能，内部自动包装为单默认组。

**验证：**
在项目根目录执行 `uv run pytest tests/test_mcp_service_smoke.py tests/test_mcp_adapter.py -v`，预期全部通过（含现有 smoke test + 新增 group-aware 场景）。

再执行 `uv run pytest -v`，预期全部通过。

## 状态
- [x] Phase 0: 数据模型与配置基础
- [x] Phase 1: AI Client 返回 usage 信息
- [x] Phase 2: Scraper category 注入
- [x] Phase 3: 管道改造（路由 + 评分 + Prompt + 渲染 + Token）
- [x] Phase 4: MCP 适配与集成回归
- [ ] 端到端验证：`uv run horizon --hours 24` 跑一次今日新闻，确认分组输出正确
- [x] 验证与审阅
