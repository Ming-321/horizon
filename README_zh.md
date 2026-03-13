<div align="center">

<img src="docs/assets/horizon-header.svg" alt="Horizon" width="100%">

**AI 筛选科技新闻，你只需阅读。**

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json&style=flat-square)](https://github.com/astral-sh/uv)
[![GitHub commit activity](https://img.shields.io/github/commit-activity/m/Ming-321/horizon?style=flat-square)](https://github.com/Ming-321/horizon/commits/main)

Horizon 从 Hacker News、RSS、Reddit、Telegram 和 GitHub 收集新闻，利用 AI 打分过滤，然后生成一份包含摘要、背景知识、社区讨论甚至 AI 播客的中英双语日报。

[在线演示](https://ming-321.github.io/horizon/) · [配置指南](https://ming-321.github.io/horizon/configuration) · [English](README.md)

</div>

## 它做什么

```
信息源               处理管道                    输出
──────────          ──────────────              ──────────────
Hacker News  ─┐                                 ┌─ Markdown 日报 (中/英)
RSS / Atom   ─┤     路由      AI 评分    背景    │
Reddit       ─┼──▶  到     ──▶ 0-10  ──▶ 知识  ─┼─ HTML 报纸版面
Telegram     ─┤     分组      & 过滤    补充    │
GitHub       ─┘                                  ├─ 微信推送 (WxPusher)
                                                 ├─ AI 播客 (MP3)
                                                 └─ GitHub Pages 站点
```

1. **抓取** — 并发拉取所有信息源
2. **去重** — 合并来自不同平台的相同 URL
3. **路由** — 将条目分配到可配置的分组（头条、关注的仓库、热榜等）
4. **评分** — AI 按技术深度、新颖性、影响力评分 0-10
5. **过滤** — 保留超过阈值的条目；特定分组可跳过评分
6. **丰富** — 搜索背景知识，收集社区讨论
7. **分类** — AI 将每组内的条目按话题聚类
8. **输出** — 生成 Markdown、HTML 报纸版面、手机简报、AI 播客

## 功能特性

### 核心能力

- **多源聚合** — Hacker News、RSS/Atom、Reddit、Telegram、GitHub（Release 与用户动态）
- **AI 评分** — 支持任何 OpenAI 兼容 API（通义千问、GPT-4、Claude、Gemini、DeepSeek 等）
- **中英双语** — 同时生成英文和中文完整报告
- **内容丰富** — 每条新闻附带背景知识和社区讨论
- **跨源去重** — 自动合并 URL 重复和话题重复

### 分组系统

条目按 category 路由到可配置的分组，每组独立控制评分 prompt、阈值和丰富模式：

```jsonc
"groups": [
  {
    "name": "头条速递",
    "categories": ["hackernews", "tech-analysis", "ai-news-zh"],
    "scoring": { "enabled": true, "threshold": 7.0 }
  },
  {
    "name": "关注动态",
    "categories": ["github-updates"],
    "scoring": { "enabled": false }  // 直通车 — 始终包含
  }
]
```

### 多渠道输出

| 渠道 | 说明 |
|------|------|
| **Markdown** | 完整的中英双语日报，含目录、评分、标签、参考链接 |
| **HTML 报告** | 报纸风格的多页面布局，侧边栏导航 + 话题分组 |
| **WxPusher 推送** | 通过微信推送完整摘要到手机 |
| **AI 播客** | LLM 生成双人对话脚本 → CosyVoice TTS 合成 → RSS 分发 |
| **GitHub Pages** | 从 `docs/` 自动部署 — 摘要、HTML 报告、播客 RSS |
| **邮件订阅** | 自托管 SMTP/IMAP 邮件列表，自动处理订阅/退订 |

### AI 播客

Horizon 可以从高分新闻自动生成每日播客：

```
精选条目 → LLM 对话脚本 → CosyVoice TTS → FFmpeg 拼接 → MP3
        → 上传到 GitHub Release → 更新 RSS feed → 播客 App 订阅
```

- 两位 AI 主持人自然对话风格
- DashScope CosyVoice 高质量中文语音合成
- 标准 Podcast RSS 2.0，兼容 AntennaPod、小宇宙等播客应用

### MCP 集成

内置 [MCP](https://modelcontextprotocol.io/) Server，AI 助手可直接驱动管道：

```bash
uv run horizon-mcp
```

管道工具：`hz_validate_config`、`hz_fetch_items`、`hz_score_items`、`hz_filter_items`、`hz_enrich_items`、`hz_generate_summary`、`hz_run_pipeline`。
运行历史：`hz_list_runs`、`hz_get_run_meta`、`hz_get_run_stage`、`hz_get_run_summary`、`hz_get_metrics`。

## 快速开始

### 1. 安装

```bash
git clone https://github.com/Ming-321/horizon.git
cd horizon
uv sync          # 或: pip install -e .
```

### 2. 配置

```bash
cp .env.example .env
cp data/config.example.json data/config.json
```

编辑 `.env` 填入 API 密钥，然后定制 `data/config.json`。完整参考见[配置指南](https://ming-321.github.io/horizon/configuration)。

### 3. 运行

```bash
uv run horizon               # 默认 24 小时窗口
uv run horizon --hours 48    # 最近 48 小时
uv run horizon --from-cache  # 从缓存重新渲染（不调 API）
```

输出路径：日报 → `data/summaries/`，HTML → `data/html/`，播客 → `data/podcasts/`。

本地预览 HTML 报告：

```bash
uv run horizon-server            # 在 http://localhost:8080 提供 data/html/
uv run horizon-server --port 3000
```

### 4. 自动化（可选）

使用 cron 或 GitHub Actions 每日定时运行：

```bash
# crontab -e
0 7 * * * cd /path/to/horizon && uv run horizon --hours 24
```

## 支持的信息源

| 信息源 | 抓取内容 | 评论收集 |
|--------|---------|---------|
| **Hacker News** | 按分数排序的热门文章 | 支持（前 N 条） |
| **RSS / Atom** | 任意订阅源 | 支持 GitHub Trending、Reddit RSS、博客等 |
| **Reddit** | Subreddit 帖子 + 用户动态 | 支持（前 N 条） |
| **Telegram** | 公开频道消息 | 网页抓取（无需 API 密钥） |
| **GitHub** | 用户动态 & 仓库 Release | 关注仓库的提交聚合 |

## 项目结构

```
src/
  ai/          — AI 客户端、评分器、摘要生成、提示词
  scrapers/    — RSS、GitHub、HN、Telegram 抓取器
  renderers/   — Brief 简报、HTML 报告、话题分类器
  services/    — 邮件、微信推送、播客管道
  mcp/         — MCP Server 集成
  storage/     — 运行历史、指标、缓存
data/          — config.json、提示词、生成的输出
tests/         — pytest 测试套件
docs/          — GitHub Pages 站点 + 设计文档
```

## 配置概览

```jsonc
{
  "ai": {
    "provider": "openai",
    "model": "qwen3.5-plus",
    "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "languages": ["zh", "en"]
  },
  "sources": { /* HN, RSS, Reddit, Telegram, GitHub */ },
  "groups": [ /* 按 category 路由，每组独立评分/丰富 */ ],
  "output": {
    "brief": { "enabled": true, "top_n": 10 },
    "html": { "enabled": true },
    "podcast": { "enabled": true, "tts_model": "cosyvoice-v3-flash" }
  },
  "notifications": {
    "wxpusher": { "enabled": true }
  }
}
```

## 致谢

本项目基于 [Thysrael/Horizon](https://github.com/Thysrael/Horizon) 改造。感谢原作者搭建了优秀的基础架构——多源聚合、AI 评分管道和 MCP 集成均源自该项目。

## 许可证

[MIT](LICENSE)
