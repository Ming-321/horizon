# Horizon

> AI-driven information aggregation system. Python 3.11+, uv 管理依赖。

## Commands

| Task | Command | ~Time |
|------|---------|-------|
| Install | `uv sync --extra dev` | ~5s |
| Test (all) | `uv run pytest -v` | ~5s |
| Test (single) | `uv run pytest tests/<file>.py -v` | ~2s |
| Run daily | `uv run horizon --hours 24` | ~5min+ |
| Run from cache | `uv run horizon --from-cache` | ~3min |
| MCP server | `uv run horizon-mcp` | long-running |
| Local preview | `uv run horizon-server` | long-running |

## Project Structure

```
src/            → Application source
  ai/           → AI client, analyzer, summarizer, prompts
  scrapers/     → RSS, GitHub, HN, Telegram scrapers
  renderers/    → Brief 简报, HTML 报告, 话题分类器
  services/     → 邮件, WxPusher, 播客管道, 本地服务器
  mcp/          → MCP server integration
  storage/      → Run history, metrics
data/           → config.json, prompts, templates, generated outputs
tests/          → pytest tests
docs/           → GitHub Pages site + plans/
scripts/        → Utility scripts
```

## Key Config

- AI 模型配置、信息源、评分阈值、分组: `data/config.json`
- API 密钥: `.env` (不提交)
- AI 评分/摘要提示词: `src/ai/prompts.py`
- 播客对话/丰富等提示词: `data/prompts/*.txt`
- HTML 报告模板: `data/templates/daily_report.html`

## Testing

- Framework: pytest
- 异步测试用 `asyncio.run()` (不用 `get_event_loop()`)
- mock httpx client 用 `AsyncMock(spec=httpx.AsyncClient)`
- 新功能必须有对应测试

## Boundaries

### Always
- 提交前运行 `uv run pytest -v`
- 修改 scraper/AI 逻辑时添加单元测试
- .env 中的密钥不得提交

### Ask First
- 添加新依赖
- 修改 `data/config.json` 的结构
- 修改 AI 提示词 (`src/ai/prompts.py` 或 `data/prompts/`)
- 修改 GitHub Actions 工作流
- 修改播客管道配置或 prompt

### Never
- 提交 `.env` 或 API 密钥
- 直接修改 `data/summaries/` 下的生成文件
- 直接修改 `data/html/`、`data/podcasts/` 下的生成文件
- 在测试中调用真实的外部 API
