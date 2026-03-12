# 阶段二：多渠道输出
> 深度: Deep

## 背景

Horizon 目前只有一种输出格式——完整的 Markdown 日报，通过 Jekyll 站点和邮件推送。路线图阶段二要求增加两个维度：

1. **简洁模式** — 适合手机端快速浏览的精简版，通过 wxpusher 推送到微信
2. **详细 HTML 模式** — 报纸风格的独立 HTML 页面，通过本地 HTTP 服务查看

现有 `DailySummarizer` 是纯程序化 Markdown 渲染器，无外部模板，内置 zh/en 双语 `LABELS`。邮件服务 `EmailManager` 直接消费 Markdown。两者均保持不变。

### 当前输出链路

```
orchestrator.run()
  ├─ DailySummarizer.generate_summary() → Markdown
  ├─ StorageManager.save_daily_summary() → data/summaries/
  ├─ _copy_to_jekyll() → docs/_posts/（front matter + Markdown）
  └─ EmailManager.send_daily_summary()（可选）
```

### 约束

- 不修改现有 `DailySummarizer`、Jekyll 输出、邮件推送
- wxpusher 的 `app_token` 和 `uids` 存 `.env`，不提交
- HTML 页面必须自包含（内联 CSS，无外部依赖），方便离线查看
- wxpusher content < 40000 字符，summary < 20 字符，QPS ≤ 2

## 参考

- `src/ai/summarizer.py` — 现有渲染器架构，`generate_summary()` 的输入格式 `Dict[str, List[ContentItem]]`
- `src/services/emailer.py` — 推送服务的接口边界参考（输入 summary_md + subject + subscribers）
- `src/orchestrator.py` L179-L210 — 当前输出阶段的集成点
- wxpusher 官方文档 — API 格式、限制、Python SDK
- `ContentItem.metadata` 字段协议 — `title_zh`, `detailed_summary_zh`, `background_zh`, `sources` 等

## 设计决策

### 1. 渲染层新增两个独立渲染器

在 `src/renderers/` 下新增：

- `brief.py` — `BriefRenderer`：简洁版 Markdown
- `html_detail.py` — `HtmlDetailRenderer`：报纸风格 HTML

两者与 `DailySummarizer` 平级，共享 `Dict[str, List[ContentItem]]` 输入格式，互不依赖。

**BriefRenderer 输出格式**（示例）：

```markdown
# Horizon 每日速递 - 2026-03-12

## 头条速递
1. ⭐ 9.2 | [标题](url) — 一句话摘要
2. ⭐ 8.7 | [标题](url) — 一句话摘要
...（Top 10）

## 关注动态
1. [Superpowers: 3 updates — ...](url)
2. [vllm: 2 updates — ...](url)

## GitHub 热榜
1. ⭐ 8.5 | [repo/name](url) — 一句话描述
...（Top 10）
```

**HtmlDetailRenderer**：
- 使用 Jinja2 模板 `data/templates/daily_report.html`
- 报纸风格：清晰的分栏、衬线标题字体、评分徽章、折叠的背景知识
- 自包含 HTML（内联 CSS + 可选 Google Fonts CDN）
- 只生成中文版

### 2. wxpusher 推送服务

新增 `src/services/wxpusher.py`：

- `WxPusherService(app_token, uids, topic_ids)`
- `push(content_html, summary)` — 推送 HTML 消息
- BriefRenderer 的 Markdown 通过 `markdown` 库转为 HTML 后推送
- 失败时 log warning，不中断主流程

### 3. 本地 HTTP 服务

新增 `src/services/local_server.py`：

- 托管 `data/html/` 目录
- 入口命令 `horizon-server --port 8080`
- Python `http.server` 即可，首页自动列出所有日报

### 4. orchestrator 集成

在现有 summary 循环后追加：

```python
if config.output.brief.enabled:
    brief_md = BriefRenderer(top_n=config.output.brief.top_n).render(grouped_for_summary, today)
    storage.save_brief(today, brief_md)

if config.output.html.enabled:
    html = HtmlDetailRenderer(template_path="data/templates/daily_report.html").render(
        grouped_for_summary, today, len(all_items)
    )
    storage.save_html(today, html)

if config.notifications.wxpusher.enabled:
    wxpusher = WxPusherService(...)
    wxpusher.push(brief_md_to_html, summary=f"Horizon 每日速递 - {today}")
```

### 5. 配置结构

`data/config.json` 新增：

```json
{
  "output": {
    "brief": { "enabled": true, "top_n": 10 },
    "html": { "enabled": true, "serve_port": 8080 }
  },
  "notifications": {
    "wxpusher": { "enabled": false }
  }
}
```

`.env` 新增：
```
WXPUSHER_APP_TOKEN=xxx
WXPUSHER_UIDS=UID_xxx
```

### 6. 新增依赖

- `jinja2` — HTML 模板渲染
- `wxpusher` — 微信推送 SDK

### 7. 实现优先级

1. BriefRenderer + WxPusherService（核心推送能力）
2. HtmlDetailRenderer + 本地服务（可视化增强）

---

## 实现计划

### Phase 0: 配置模型扩展 + 新增依赖

**涉及文件：**
- 修改: `src/models.py`（新增 `OutputConfig`、`BriefConfig`、`HtmlConfig`、`NotificationsConfig`、`WxPusherConfig`，`Config` 新增 `output` 和 `notifications` 字段）
- 修改: `data/config.json`（新增 `output` 和 `notifications` 顶级键）
- 修改: `data/config.example.json`（同步）
- 修改: `pyproject.toml`（新增 `jinja2` 和 `wxpusher` 依赖 + `horizon-server` 脚本入口）
- 修改: `src/main.py`（更新 `print_config_template()` 内嵌模板，使其包含 `output`/`notifications` 字段，或改为读取 `data/config.example.json`）
- 修改: `tests/test_models_groups.py`（验证新配置模型字段）

**关键改动：**

`src/models.py` 新增：
```python
class BriefConfig(BaseModel):
    enabled: bool = False
    top_n: int = 10

class HtmlConfig(BaseModel):
    enabled: bool = False
    serve_port: int = 8080

class OutputConfig(BaseModel):
    brief: BriefConfig = Field(default_factory=BriefConfig)
    html: HtmlConfig = Field(default_factory=HtmlConfig)

class WxPusherConfig(BaseModel):
    enabled: bool = False
    app_token_env: str = "WXPUSHER_APP_TOKEN"
    uids_env: str = "WXPUSHER_UIDS"
    topic_ids_env: str = "WXPUSHER_TOPIC_IDS"

class NotificationsConfig(BaseModel):
    wxpusher: WxPusherConfig = Field(default_factory=WxPusherConfig)

class Config(BaseModel):
    # ...existing fields...
    output: OutputConfig = Field(default_factory=OutputConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
```

`pyproject.toml` dependencies 新增：
```
"jinja2>=3.1.0",
"wxpusher>=3.0.0",
```

`pyproject.toml` scripts 新增：
```
horizon-server = "src.services.local_server:main"
```

`data/config.json` 新增顶级键：
```json
{
  "output": {
    "brief": { "enabled": true, "top_n": 10 },
    "html": { "enabled": true, "serve_port": 8080 }
  },
  "notifications": {
    "wxpusher": { "enabled": false }
  }
}
```

**验证：**
在项目根目录执行：
1. `uv sync --extra dev && uv run python -c "import jinja2, wxpusher"` — 预期无报错
2. `uv run pytest tests/test_models_groups.py -v` — 预期全部通过
3. `uv run horizon-server --help` — 预期打印帮助信息

### Phase 1: BriefRenderer — 简洁版渲染

**涉及文件：**
- 新增: `src/renderers/` 目录（需创建）
- 新增: `src/renderers/__init__.py`
- 新增: `src/renderers/brief.py`（`BriefRenderer` 类）
- 修改: `src/storage/manager.py`（新增 `save_brief()` 方法）
- 新增: `tests/test_brief_renderer.py`（含渲染测试和存储测试）

**关键改动：**

`src/renderers/brief.py`:
```python
class BriefRenderer:
    def __init__(self, top_n: int = 10):
        self.top_n = top_n

    def render(
        self,
        grouped_items: Dict[str, List[ContentItem]],
        date: str,
    ) -> str:
        """生成简洁版中文 Markdown 摘要。

        每组最多 top_n 条，格式：
        ## {group_name}
        1. ⭐ {score} | [{title}]({url}) — {one_line_summary}
        """
```

取每组 items 的前 `top_n` 条（已按 ai_score 降序排列）。

标题取值优先级：`metadata["title_zh"]` → `item.title`。

一句话摘要取值优先级：`metadata["detailed_summary_zh"]` → `metadata["detailed_summary"]` → `item.ai_summary` → `item.title`。
分句规则：按 `。！？.!?` 分句，取第一句；若分句后为空或原文无标点，直接使用完整文本截断到 80 字符。

评分显示规则：`item.ai_score is None` 时隐藏 ⭐ 评分（bypass scoring 的组天然满足此条件）。

`src/storage/manager.py` 新增：
```python
def save_brief(self, date: str, markdown: str) -> Path:
    path = self.summaries_dir / f"horizon-{date}-brief.md"
    path.write_text(markdown, encoding="utf-8")
    return path
```

测试覆盖：
- 正常渲染多组 Top N
- 空组跳过
- `ai_score is None` 时不显示评分
- 摘要 fallback 链（各字段缺失场景）
- 无中文标点时的截断
- `save_brief()` 文件落盘

**验证：**
在项目根目录执行 `uv run pytest tests/test_brief_renderer.py -v`，预期全部通过。

### Phase 2: WxPusherService — 微信推送

**涉及文件：**
- 新增: `src/services/wxpusher.py`（`WxPusherService` 类）
- 新增: `tests/test_wxpusher.py`

**关键改动：**

`src/services/wxpusher.py`:
```python
import logging
import os
from wxpusher import WxPusher

logger = logging.getLogger(__name__)

class WxPusherService:
    def __init__(self, config: WxPusherConfig):
        self.app_token = os.getenv(config.app_token_env, "")
        self.uids = [u.strip() for u in os.getenv(config.uids_env, "").split(",") if u.strip()]
        self.topic_ids = [int(t.strip()) for t in os.getenv(config.topic_ids_env, "").split(",") if t.strip()]

    def push(self, content_html: str, summary: str) -> bool:
        """推送 HTML 消息到微信。

        内部负责约束校验：
        - summary 截断到 20 字符
        - content_html 截断到 40000 字符（在闭合标签处截断）
        - app_token 为空时跳过并返回 False

        使用 wxpusher SDK: WxPusher.send_message(content, token=..., uids=[...],
        topic_ids=[...], content_type=2, summary=...)
        content_type=2 表示 HTML 格式。

        每次 run 最多一次 API 调用，天然满足 QPS ≤ 2。
        """
```

纯推送客户端封装，不包含 Markdown→HTML 转换逻辑（转换由调用方在 orchestrator 中处理）。

测试覆盖（mock `WxPusher.send_message`）：
- 正常推送成功（验证 content_type=2）
- token 为空时跳过，返回 False
- API 调用异常时 log warning 不抛异常
- summary 超长截断
- content 超长截断

**验证：**
在项目根目录执行 `uv run pytest tests/test_wxpusher.py -v`，预期全部通过。

### Phase 3: HtmlDetailRenderer + 本地 HTTP 服务

**涉及文件：**
- 新增: `src/renderers/html_detail.py`（`HtmlDetailRenderer` 类）
- 新增: `data/templates/` 目录（需创建）
- 新增: `data/templates/daily_report.html`（Jinja2 模板）
- 修改: `src/storage/manager.py`（新增 `save_html()` 方法）
- 新增: `src/services/local_server.py`（简单 HTTP 服务）
- 新增: `tests/test_html_renderer.py`（含渲染测试和存储测试）

**关键改动：**

`src/renderers/html_detail.py`:
```python
from jinja2 import Environment, FileSystemLoader

class HtmlDetailRenderer:
    def __init__(self, template_dir: str = "data/templates"):
        self.env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
        self.template = self.env.get_template("daily_report.html")

    def render(
        self,
        grouped_items: Dict[str, List[ContentItem]],
        date: str,
        total_fetched: int,
    ) -> str:
        """生成报纸风格的自包含 HTML 页面（中文版）。"""
        return self.template.render(
            date=date,
            total_fetched=total_fetched,
            total_selected=sum(len(items) for items in grouped_items.values()),
            groups=[...],
        )

    def _prepare_item(self, item: ContentItem) -> dict:
        """将 ContentItem 转为模板友好的 dict。

        返回字段及来源：
        - title: metadata["title_zh"] → item.title
        - url: str(item.url)
        - score: item.ai_score（None 时模板隐藏）
        - summary: metadata["detailed_summary_zh"] → metadata["detailed_summary"] → item.ai_summary → ""
        - background: metadata["background_zh"] → metadata["background"] → ""
        - discussion: metadata["community_discussion_zh"] → metadata["community_discussion"] → ""
        - sources: metadata["sources"] → []（列表，每项 {url, title}）
        - tags: item.ai_tags → []
        - source_line: "{source_type} · {feed_name/author} · {date}"
        - published_at: item.published_at（datetime or None）
        """
```

`data/templates/daily_report.html`：
- 报纸风格设计：衬线中文字体（系统字体栈：`"Noto Serif SC", "Source Han Serif SC", "Songti SC", serif`，不使用 CDN 外链）
- 分栏布局、评分徽章（圆角色块）、折叠的背景知识（`<details>`）
- 完全内联 `<style>` 标签，自包含，可离线查看
- 响应式：桌面端双栏，手机端单栏
- 配色：深灰背景标题栏 + 白色内容卡片 + 蓝色链接

`src/storage/manager.py` 新增：
```python
def save_html(self, date: str, html: str) -> Path:
    html_dir = self.data_dir / "html"
    html_dir.mkdir(exist_ok=True)
    path = html_dir / f"horizon-{date}.html"
    path.write_text(html, encoding="utf-8")
    return path
```

`src/services/local_server.py`：
```python
import http.server
import os
from pathlib import Path

def main():
    """CLI 入口：horizon-server [--port PORT]"""
    import argparse
    parser = argparse.ArgumentParser(description="Horizon HTML report server")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    html_dir = Path("data/html")
    html_dir.mkdir(parents=True, exist_ok=True)

    os.chdir(html_dir)
    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.HTTPServer(("0.0.0.0", args.port), handler)
    print(f"Serving Horizon reports at http://localhost:{args.port}")
    server.serve_forever()
```

测试覆盖：
- 模板加载和渲染不报错
- `_prepare_item()` 字段 fallback 链正确
- 输出包含关键 HTML 结构（分组名、评分徽章等）
- 空 items 处理
- `save_html()` 文件落盘到 `data/html/`

**验证：**
1. 在项目根目录执行 `uv run pytest tests/test_html_renderer.py -v`，预期全部通过
2. 在项目根目录执行 `uv run horizon-server --port 8080`，预期终端打印 `Serving Horizon reports at http://localhost:8080`

### Phase 4: Orchestrator 集成 + 集成测试

**涉及文件：**
- 修改: `src/orchestrator.py`（在 summary 循环后追加 brief/html 渲染和 wxpusher 推送）
- 新增: `tests/test_output_integration.py`（集成测试）

**关键改动：**

在 `run()` 方法的 summary 生成循环之后追加（wxpusher 作为独立分支，不嵌套在 brief 内）：

```python
import markdown as md_lib
from .renderers.brief import BriefRenderer
from .renderers.html_detail import HtmlDetailRenderer
from .services.wxpusher import WxPusherService

# Brief 渲染
brief_md = None
if self.config.output.brief.enabled:
    brief_renderer = BriefRenderer(top_n=self.config.output.brief.top_n)
    brief_md = brief_renderer.render(grouped_for_summary, today)
    brief_path = self.storage.save_brief(today, brief_md)
    logger.info("brief: saved to %s", brief_path)
    self.console.print(f"\N{NEWSPAPER} Saved brief summary to: {brief_path}\n")

# wxpusher 推送（独立开关，按需生成 brief）
if self.config.notifications.wxpusher.enabled:
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

# HTML 详细版渲染
if self.config.output.html.enabled:
    html_renderer = HtmlDetailRenderer()
    html_content = html_renderer.render(grouped_for_summary, today, len(all_items))
    html_path = self.storage.save_html(today, html_content)
    logger.info("html: saved to %s", html_path)
    self.console.print(f"\N{GLOBE WITH MERIDIANS} Saved HTML report to: {html_path}\n")
```

集成测试 `tests/test_output_integration.py` 覆盖：
- `brief.enabled=True` + `wxpusher.enabled=False` → brief 文件生成，wxpusher 不调用
- `brief.enabled=False` + `wxpusher.enabled=True` → brief 文件不生成，但 wxpusher 仍能推送
- `html.enabled=True` → HTML 文件落盘到 `data/html/`
- wxpusher 推送失败不阻断主流程
- 所有开关关闭时无副作用

**验证：**
在项目根目录执行 `uv run pytest -v`，预期全部测试通过（含所有新增测试）。

## 状态
- [x] Phase 0: 配置模型扩展 + 新增依赖
- [x] Phase 1: BriefRenderer — 简洁版渲染
- [x] Phase 2: WxPusherService — 微信推送
- [x] Phase 3: HtmlDetailRenderer + 本地 HTTP 服务
- [x] Phase 4: Orchestrator 集成 + 集成测试
- [x] 验证与审阅
