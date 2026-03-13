# 播客 RSS 分发 — Cloudflare R2 托管
> 深度: Lightweight

## 问题分析

播客管道已生成本地 MP3，但无法传达到手机。服务器仅暴露 80 端口（Zeabur ingress），其他端口被安全组阻断，自建 HTTP 服务器方案不可行。

需要：
1. 将 MP3 上传到可公开访问的存储
2. 生成标准 Podcast RSS feed，手机播客 app 可订阅

## 修复方案

### 存储：Cloudflare R2

- 免费 10GB + 零流出费用
- 启用 `r2.dev` 公开子域名，无需自有域名
- Python SDK: `boto3`（S3 兼容 API）

### 核心流程

```
PodcastPipeline.generate() 生成 MP3
  → R2Uploader.upload(mp3_path)
  → FeedGenerator.update_feed(episode_meta)
  → R2Uploader.upload(feed.xml)
```

### 新增/修改文件

| 文件 | 改动 |
|------|------|
| `src/services/podcast.py` | 新增 `R2Uploader` 类（boto3 S3 兼容上传）；新增 `FeedGenerator` 类（生成 RSS XML）；`PodcastPipeline.generate()` 末尾调用上传+更新 feed |
| `src/models.py` | `PodcastConfig` 新增字段：`r2_bucket`, `r2_endpoint`, `r2_public_url`, `feed_title`, `feed_description` |
| `data/config.json` | podcast 节点新增 R2 配置 |
| `data/config.example.json` | 同步 |
| `.env` | 新增 `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |
| `pyproject.toml` | 新增 `boto3` 依赖 |
| `tests/test_podcast.py` | 新增 R2Uploader / FeedGenerator 单元测试 |

### R2Uploader 设计

```python
class R2Uploader:
    def __init__(self, bucket: str, endpoint: str, public_url: str):
        # boto3 S3 client, endpoint_url=endpoint
        # 从 env 读 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY

    async def upload(self, local_path: Path, remote_key: str) -> str:
        # asyncio.to_thread 包装 s3.upload_file()
        # 返回公开 URL: f"{public_url}/{remote_key}"
```

### FeedGenerator 设计

```python
class FeedGenerator:
    def __init__(self, title: str, description: str, feed_url: str):
        pass

    def update_feed(self, episode: EpisodeMeta, existing_xml: Optional[str]) -> str:
        # 用 xml.etree.ElementTree 生成标准 Podcast RSS 2.0
        # 保留历史 episode，新 episode 插入最前
        # 返回 XML 字符串
```

RSS 2.0 必备字段：`<channel>` (title, link, description, language), `<item>` (title, enclosure[url, length, type], pubDate, description)

### 配置结构

```json
{
  "output": {
    "podcast": {
      "enabled": true,
      "r2_bucket": "horizon-podcast",
      "r2_endpoint": "https://<account_id>.r2.cloudflarestorage.com",
      "r2_public_url": "https://pub-<hash>.r2.dev",
      "feed_title": "Horizon 科技日报",
      "feed_description": "AI 生成的每日科技新闻播客"
    }
  }
}
```

### 安全

- R2 API 密钥存 `.env`，不入库
- `r2.dev` 子域名长随机 hash，不易猜测
- bucket 设为公开只读

### 验收标准

1. `uv run pytest -v` 全部通过
2. MP3 可通过 R2 公开 URL 下载
3. feed.xml 符合 Podcast RSS 2.0 规范
4. 手机播客 app（小宇宙/Pocket Casts）可订阅并播放

---

## 实现计划

### Phase 0: 基础设施 — 配置模型 + 依赖 + R2Uploader + FeedGenerator

**涉及文件：**
- 修改: `src/models.py`（PodcastConfig 新增 5 个字段）
- 修改: `pyproject.toml`（新增 boto3 依赖）
- 修改: `data/config.json`（podcast 节点新增 R2 + feed 配置）
- 修改: `data/config.example.json`（同步）
- 修改: `src/services/podcast.py`（新增 R2Uploader + FeedGenerator 类）
- 修改: `tests/test_podcast.py`（新增 R2Uploader / FeedGenerator 单元测试）
- 修改: `tests/test_models_groups.py`（PodcastConfig 新字段测试）

**关键改动：**

`src/models.py` — PodcastConfig 新增字段（都有空默认值，保持向后兼容）：

```python
class PodcastConfig(BaseModel):
    # ... 已有字段 ...
    r2_bucket: str = ""
    r2_endpoint: str = ""
    r2_public_url: str = ""
    feed_title: str = "Horizon 科技日报"
    feed_description: str = "AI 生成的每日科技新闻播客"
```

`pyproject.toml` — 新增：

```toml
"boto3>=1.35.0",
```

`src/services/podcast.py` — 新增两个类：

```python
class R2Uploader:
    def __init__(self, bucket: str, endpoint: str, public_url: str):
        """构建 boto3 S3 client，从 env 读 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY"""

    async def upload(self, local_path: Path, remote_key: str) -> str:
        """asyncio.to_thread 包装 s3.upload_file()，返回公开 URL"""

    async def download_text(self, remote_key: str) -> Optional[str]:
        """下载文本文件（用于读取已有 feed.xml）"""

class FeedGenerator:
    def __init__(self, title: str, description: str, public_url: str):
        pass

    def update_feed(self, date: str, audio_url: str, audio_size: int,
                    existing_xml: Optional[str] = None) -> str:
        """xml.etree.ElementTree 生成 Podcast RSS 2.0 XML
        - 解析 existing_xml 保留历史 episode
        - 新 episode 插入 <channel> 最前
        - 返回完整 XML 字符串"""
```

**验证：**
在项目根目录执行 `uv sync --extra dev && uv run pytest tests/test_models_groups.py tests/test_podcast.py -v`，预期全部通过。

### Phase 1: 集成 — PodcastPipeline 串联上传 + feed 更新

**涉及文件：**
- 修改: `src/services/podcast.py`（PodcastPipeline.generate 末尾新增上传 + feed 逻辑）
- 修改: `tests/test_podcast.py`（集成测试：pipeline 生成后调用上传）

**关键改动：**

`src/services/podcast.py` — `PodcastPipeline.generate()` 在 `AudioMerger.merge()` 成功后：

```python
# merge 成功后
if result and self.config.r2_bucket:
    uploader = R2Uploader(
        self.config.r2_bucket,
        self.config.r2_endpoint,
        self.config.r2_public_url,
    )
    remote_key = f"episodes/horizon-{date}.mp3"
    audio_url = await uploader.upload(result, remote_key)
    logger.info("podcast: uploaded to %s", audio_url)

    # 下载已有 feed / 生成新 feed
    existing = await uploader.download_text("feed.xml")
    feed_gen = FeedGenerator(
        self.config.feed_title,
        self.config.feed_description,
        self.config.r2_public_url,
    )
    feed_xml = feed_gen.update_feed(date, audio_url, result.stat().st_size, existing)

    feed_local = result.parent / "feed.xml"
    feed_local.write_text(feed_xml, encoding="utf-8")
    await uploader.upload(feed_local, "feed.xml")
    logger.info("podcast: feed.xml updated")
```

R2 配置为空时（`r2_bucket == ""`）跳过上传，保持纯本地行为（向后兼容）。

**验证：**
在项目根目录执行 `uv run pytest -v`，预期全部通过。

## 状态
- [x] Phase 0: 基础设施 — 配置模型 + 依赖 + R2Uploader + FeedGenerator
- [x] Phase 1: 集成 — PodcastPipeline 串联上传 + feed 更新
- [x] 验证与审阅
