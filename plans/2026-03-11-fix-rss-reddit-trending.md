# 修复三个数据源抓取失败问题
> 深度: Lightweight

## 问题分析

### Bug 1：量子位 RSS 403 Forbidden

- **症状**：`Error fetching RSS feed 量子位: Client error '403 Forbidden' for url 'https://www.qbitai.com/feed'`
- **根因**：httpx 默认 User-Agent 为 `python-httpx/0.28.1`，量子位服务器的 WAF 拒绝非浏览器 UA
- **验证**：`python-httpx/0.28.1` → 403；`Mozilla/5.0 (compatible; Horizon/1.0)` → 200

### Bug 2：GitHub Trending Daily 返回 0 条

- **症状**：GitHub Trending RSS 源始终返回 0 条内容（feed 实际有 13 个条目）
- **根因**：该 feed 的 `<item>` 没有任何日期字段（`published`/`updated`/`created` 均不存在），`RSSScraper._parse_date()` 返回 None，被 `if not published_at` 过滤
- **验证**：`feedparser.parse()` 显示条目仅有 `title`/`links`/`link`/`summary`/`media_content`，无日期；Channel 级有 `pubDate`

### Bug 3：Reddit 403 Blocked

- **症状**：`Client error '403 Blocked' for url 'https://www.reddit.com/r/MachineLearning/hot.json'`
- **根因**：Reddit 对 `www.reddit.com` 的 JSON API 强制 OAuth，无 token 一律 403
- **验证**：`www.reddit.com/.json` → 403；`old.reddit.com/.rss` → 200（带浏览器 UA）

## 修复方案

### Fix 1：统一设置浏览器 User-Agent

**文件**：`src/orchestrator.py`

**改动**：在 `fetch_all_sources()` 中创建 `httpx.AsyncClient` 时设置 headers。

```python
# 修改前
async with httpx.AsyncClient(timeout=30.0) as client:

# 修改后
headers = {"User-Agent": "Mozilla/5.0 (compatible; Horizon/1.0; +https://github.com/Thysrael/Horizon)"}
async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
```

**影响范围**：所有通过此 client 发出的请求（GitHub、HN、RSS、Reddit、Telegram）都会使用新 UA。这不会导致副作用——大多数服务对浏览器 UA 更友好。

### Fix 2：无日期条目的 fallback 逻辑

**文件**：`src/scrapers/rss.py`

**改动**：在 `_fetch_feed()` 中，当 `_parse_date()` 返回 None 时，使用 feed 的 channel 级 pubDate 或当前时间作为 fallback。

```python
# 修改前
published_at = self._parse_date(entry)
if not published_at or published_at < since:
    continue

# 修改后
published_at = self._parse_date(entry)
if not published_at:
    published_at = channel_pub_date or datetime.now(timezone.utc)
if published_at < since:
    continue
```

其中 `channel_pub_date` 从 `feed.feed` 的 `published_parsed` 或 `updated_parsed` 中提取。

**影响范围**：仅影响没有条目级日期的 feed（如 GitHub Trending）。有日期的 feed 不受影响。

### Fix 3：Reddit 改用 RSS 订阅

**文件**：`data/config.json`

**改动**：禁用 Reddit 专用抓取器，将 Reddit 子版块改为 RSS 源：

```json
// 禁用 Reddit 抓取器
"reddit": {
  "enabled": false,
  ...
}

// 在 rss 数组中添加
{
  "name": "r/MachineLearning",
  "url": "https://old.reddit.com/r/MachineLearning/hot/.rss?limit=15",
  "enabled": true,
  "category": "reddit-ml"
},
{
  "name": "r/LocalLLaMA",
  "url": "https://old.reddit.com/r/LocalLLaMA/hot/.rss?limit=15",
  "enabled": true,
  "category": "reddit-llm"
}
```

**权衡**：丢失评论抓取能力（Reddit JSON API 原本也 403，评论本就拿不到）。RSS 提供标题、链接、正文摘要，足够 AI 评分。

### 验证计划

1. 运行 `uv run pytest` 确认无回归
2. 运行 `uv run horizon --hours 24` 验证：
   - 量子位不再 403，能抓到条目
   - GitHub Trending 返回 >0 条内容
   - Reddit 子版块能通过 RSS 抓到条目

---

## 实现计划

### Phase 0A: UA 修复

**涉及文件：**
- 修改: `src/orchestrator.py`（httpx 客户端添加浏览器 UA）

**关键改动：**

`src/orchestrator.py` 第 188 行：

```python
headers = {"User-Agent": "Mozilla/5.0 (compatible; Horizon/1.0; +https://github.com/Thysrael/Horizon)"}
async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
```

**验证：**
在项目根目录执行 `uv run pytest -v`（确认现有 17 个 MCP 测试无回归）。
然后用 curl 级验证：`python -c "import httpx; ..."`，确认量子位 feed 不再 403。

### Phase 0B: RSS 日期 fallback

**涉及文件：**
- 修改: `src/scrapers/rss.py`（无日期条目的 fallback 逻辑）

**关键改动：**

`src/scrapers/rss.py` `_fetch_feed()` 方法：

在 `feed = feedparser.parse(response.text)` 之后、`for entry in feed.entries:` 之前，从 feed 的 channel 级提取 pubDate：

```python
channel_pub_date = None
for field in ("published_parsed", "updated_parsed"):
    parsed = feed.feed.get(field)
    if parsed:
        channel_pub_date = datetime.fromtimestamp(
            calendar.timegm(parsed), tz=timezone.utc
        )
        break
```

在条目日期检查处：

```python
published_at = self._parse_date(entry)
if not published_at:
    published_at = channel_pub_date or datetime.now(timezone.utc)
if published_at < since:
    continue
```

注意：`import calendar` 已存在于 `rss.py` 第 3 行，无需新增。

**验证：**
新增定向单元测试 `tests/test_rss_date_fallback.py`：
- 用伪造 feed（entry 无日期字段、channel 有 pubDate）验证 fallback 到 channel_pub_date
- 用伪造 feed（entry 和 channel 均无日期字段）验证 fallback 到 `datetime.now()`
执行 `uv run pytest tests/test_rss_date_fallback.py -v`，预期全部通过。

### Phase 1: Reddit 配置迁移（依赖 Phase 0A）

**涉及文件：**
- 修改: `data/config.json`（禁用 Reddit 抓取器，添加 RSS 源）

**关键改动：**

将 `sources.reddit.enabled` 设为 `false`。

在 `sources.rss` 数组中追加两个 Reddit RSS 源：

```json
{
  "name": "r/MachineLearning",
  "url": "https://old.reddit.com/r/MachineLearning/hot/.rss?limit=15",
  "enabled": true,
  "category": "reddit-ml"
},
{
  "name": "r/LocalLLaMA",
  "url": "https://old.reddit.com/r/LocalLLaMA/hot/.rss?limit=15",
  "enabled": true,
  "category": "reddit-llm"
}
```

**验证：**
在项目根目录执行 `set -o pipefail && uv run horizon --hours 24 2>&1 | tee /tmp/horizon-verify.log`，检查完整日志：
- `grep -c "403 Forbidden\|403 Blocked" /tmp/horizon-verify.log` 应为 0
- `grep "RSS Feeds" /tmp/horizon-verify.log` 应包含量子位
- `grep "GitHub Trending" /tmp/horizon-verify.log` 后的条目数应 > 0
- 不应出现 `Fetching from Reddit` 日志行
- 应出现 `r/MachineLearning` 和 `r/LocalLLaMA` 作为 RSS 源

## 状态
- [x] Phase 0A: UA 修复
- [x] Phase 0B: RSS 日期 fallback
- [x] Phase 1: Reddit 配置迁移
- [x] 验证与审阅
