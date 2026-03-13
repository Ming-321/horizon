# Horizon 开发路线图

## 阶段一：核心架构升级

> 分组 + 自定义 Prompt + 直通车 + API 用量统计

**需求**：
1. **分组（Sections）**：按 category 将内容路由到不同分组（通用新闻、AI 新闻、GitHub 热榜、关注的仓库更新等）
2. **自定义 Prompt**：每个分组可配置独立的评分 prompt 和摘要 prompt，不指定则 fallback 到全局默认。摘要 prompt 应支持控制输出长度和风格（如"新闻报道风格、每条 200-400 字"），解决当前日报内容过短的问题
3. **直通车**：特定分组跳过 AI 评分，直接进入输出（如关注的仓库更新）
4. **API 用量统计**：记录每次 AI 调用的 token 消耗，运行结束时汇总输出

**关键改动**：
- `data/config.json` 新增 `groups` 配置（分组定义 + 每组 prompt/bypass/threshold）
- `src/orchestrator.py` 管道改为 group-aware（路由 → 条件评分 → 按组聚合）
- `src/ai/client.py` 返回 usage 信息
- `src/ai/analyzer.py` 接受外部 prompt 参数
- `src/ai/summarizer.py` 按组分区渲染
- prompt 模板用外部文件（`data/prompts/`），避免 JSON 中嵌入长文本

**配置结构设计**：
```json
{
  "groups": [
    {
      "id": "general_news",
      "name": "通用新闻",
      "categories": ["tech-analysis"],
      "scoring": {
        "enabled": true,
        "prompt_file": null,
        "threshold": 7.0
      }
    },
    {
      "id": "watched_repos",
      "name": "关注的仓库",
      "categories": ["github-updates"],
      "scoring": {
        "enabled": false
      }
    }
  ]
}
```

**预估工作量**：中大

---

## 阶段二：多渠道输出

> 简洁/详细显示模式 + wxpusher 微信推送

**需求**：
5. **简洁模式**：标题 + 一句话摘要 + 链接，推送到手机（通勤路上快速浏览）
6. **详细模式**：完整 HTML 页面，包含分组、背景知识、评分等全部信息，本地 web server 提供
7. **wxpusher 推送**：与邮件并行的推送渠道，支持 HTML/Markdown 格式

**关键改动**：
- `src/ai/summarizer.py` 拆为 `BriefRenderer`（简洁）+ `DetailedRenderer`（Jinja2 HTML）
- 新增 `src/services/wxpusher.py`（wxpusher Python SDK 集成）
- 详细报告通过 Nginx 静态托管（与阶段三播客共用同一 Nginx + Basic Auth，端口 18080）
- `data/config.json` 新增 `notifications.wxpusher` 和 `output.mode` 配置
- 推送策略：按组控制（哪些组推手机、哪些只在网页显示）

**wxpusher 技术要点**：
- 消息格式：纯文本 / HTML（推荐）/ Markdown
- content < 40000 字符，summary < 20 字符
- QPS ≤ 2，单 UID 日接收 ≤ 2000 条
- 依赖：`pip install wxpusher`

**预估工作量**：中

---

## 阶段三：播客生成

> 核心新闻 → AI 播客（双人对话 + TTS 语音合成）

**需求**：
8. 从高分新闻中选取核心内容，用 LLM 生成双人对话脚本（类似 NotebookLM Audio Overview）
9. 使用 TTS 模型将对话脚本合成为音频文件
10. 生成播客链接，方便收听

### 技术方案选型

**结论：轻量自建**（参考 podcastfy 架构设计，不直接依赖）

选型理由：
- podcastfy（6k 星）是最成熟的开源方案，但中文支持有已知 bug（[issue#59](https://github.com/souzatharsis/podcastfy/issues/59)），fork 修复成本不低于自建
- 302_podcast_generator 中文支持好但依赖 pnpm + MongoDB，架构过重
- Horizon 的新闻数据已在内存中，不需要内容提取模块，只需"脚本生成 + TTS"两步
- 已有 DashScope 全家桶（Qwen3.5-plus + CosyVoice），同一 API Key，零额外成本

参考项目：
- [podcastfy](https://github.com/souzatharsis/podcastfy) — 对话 prompt 设计、脚本结构、TTS 工厂模式
- [Personal-AI-News-Podcast](https://github.com/xl631212/Personal-AI-News-Podcast) — 新闻播客的选题策略
- [AI-Podcast-Producer](https://github.com/slfagrouche/AI-Podcast-Producer) — 端到端新闻播客自动化流程

### 工作流

```
高分新闻条目
    ↓
Qwen3.5-plus 生成对话脚本（JSON: [{speaker, text}, ...]）
    ↓
CosyVoice v3-flash 分角色合成语音（WebSocket）
    ↓
FFmpeg 拼接音频段 → MP3
    ↓
web server 托管 + wxpusher 推送链接
```

### 对话脚本生成

- LLM：当前 Qwen3.5-plus 即可，无需额外模型
- Prompt 设计：两位主持人角色（如"资深科技记者 A + 好奇听众 B"）
  - 自然对话风格，包含提问、回应、幽默过渡，避免读稿感
  - 输出格式：JSON 数组 `[{"speaker": "A", "text": "..."}, {"speaker": "B", "text": "..."}]`
  - 参考 podcastfy 的 conversation prompt（分段式、含停顿标记）
- 脚本长度：5-10 分钟（约 1500-3000 字中文）

### TTS 方案

| 方案 | 质量 | 价格 | 中文 | 集成 | 备注 |
|------|------|------|------|------|------|
| **CosyVoice v3-flash**（推荐） | MOS 5.53 | $0.13/万字符 | 原生强 | WebSocket | 新加坡节点可用，与 LLM 共用账号 |
| CosyVoice v3-plus | 更高 | $0.26/万字符 | 原生强 | WebSocket | 质量优先时选择 |
| Qwen3-TTS | 高 | 待定 | 原生强 | WebSocket | 支持情感控制/声音设计 |
| Edge TTS | 中等 | 免费 | 支持 | pip 库 | 开发测试阶段可用 |

**推荐**：CosyVoice v3-flash（性价比最优，与现有 DashScope 账号共用）
- 端点：`wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference`
- Python SDK：`dashscope.audio.tts_v2.SpeechSynthesizer`
- 双人对话：speaker A/B 分别用不同 voice 参数合成，FFmpeg 顺序拼接
- 费用估算：3000 字脚本 ≈ $0.04（flash）

### 音频托管与分发

**部署方案：公网 IP + 非标准端口 + HTTP Basic Auth**

AntennaPod 3.x 已修复 Basic Auth 认证（[#5855](https://github.com/AntennaPod/AntennaPod/issues/5855)，v2.6.0+），社区有自托管成功案例。备选 App：Podcast Addict（认证支持最稳）。

```
服务器（Nginx，端口 18080，Basic Auth）
├── /podcast/feed.xml          ← 播客 RSS
├── /podcast/2026-03-11.mp3    ← 每日音频
└── /podcast/2026-03-10.mp3

手机 AntennaPod
└── 订阅 http://user:pass@公网IP:18080/podcast/feed.xml
    └── 后台自动刷新 + 下载
```

Nginx 配置：
```nginx
server {
    listen 18080;
    server_name _;
    location /podcast/ {
        alias /var/www/horizon/podcasts/;
        auth_basic "Private";
        auth_basic_user_file /etc/nginx/.htpasswd;
        autoindex off;
    }
}
```

安全加固：非标准端口避开扫描 + Basic Auth + 可选 ufw 限制源 IP + 可选 fail2ban

存储路径：`/var/www/horizon/podcasts/`（Horizon 生成后直接写入）

feed.xml：每次生成新播客后 Python 脚本自动更新 RSS XML（含 `<enclosure>` 标签）

推送集成：wxpusher 消息附带播客链接 + 文字版摘要

### 关键改动

- 新增 `src/services/podcast.py`
  - `ScriptGenerator`：调用 LLM 生成对话脚本
  - `TTSSynthesizer`：调用 CosyVoice 分角色合成
  - `AudioMerger`：FFmpeg 拼接音频段
  - `PodcastPipeline`：串联以上三步
- 新增 `data/prompts/podcast_dialogue.txt`（对话生成 prompt）
- `pyproject.toml` 新增依赖：`dashscope`（TTS SDK）
- 系统依赖：FFmpeg（`apt install ffmpeg`）
- `data/config.json` 新增 `podcast` 配置：
  ```json
  {
    "podcast": {
      "enabled": false,
      "tts_model": "cosyvoice-v3-flash",
      "voice_a": "longanyang",
      "voice_b": "longxiaochun",
      "max_script_chars": 3000,
      "output_dir": "data/podcasts"
    }
  }
  ```

### 成本与限制

- TTS 费用：每期 ~$0.04（3000 字 flash）~ $0.08（plus）
- LLM 费用：对话脚本生成 ~2000-4000 token
- 生成耗时：脚本 ~10s + TTS ~30-60s + 拼接 ~5s
- 新加坡节点限制：不支持语音克隆/设计（仅北京节点支持 v3.5）

**预估工作量**：中大

---

## 依赖关系

```
阶段一（核心架构）
    ↓
阶段二（多渠道输出）  ←  依赖分组结构
    ↓
阶段三（播客生成）    ←  依赖分组 + 评分 + web server
```

阶段二和三有顺序依赖：播客音频需要 web server 托管，播客链接需要通过 wxpusher/邮件推送。
