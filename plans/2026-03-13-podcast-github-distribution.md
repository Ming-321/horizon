# 播客分发 — GitHub Release + Pages
> 深度: Lightweight

## 问题分析

播客 MP3 已生成，需要分发到手机。服务器只有 80 端口对外开放（Zeabur ingress），其他端口被安全组阻断。R2 需要绑卡。

仓库已改为公开，GitHub Pages 已启用（`docs/` 目录，main 分支）。

## 修复方案

### 存储方案

- **音频**：GitHub Release asset（每个 release 最大 2GB，一天一个 ~7M MP3）
- **RSS feed**：`docs/podcast/feed.xml`，通过 GitHub Pages 公开访问

### 核心流程

```
PodcastPipeline.generate() 生成 MP3
  → GitHubUploader.create_release_and_upload(mp3)  # GitHub API
  → FeedGenerator.update_feed(episode_meta)          # 已有
  → git commit + push docs/podcast/feed.xml          # 更新 Pages
```

### 新增/修改文件

| 文件 | 改动 |
|------|------|
| `src/services/podcast.py` | 新增 `GitHubUploader` 类（httpx + GitHub API 创建 release + 上传 asset）；修改 `_upload_and_update_feed` 切换到 GitHub 方案 |
| `src/models.py` | `PodcastConfig` 新增 `github_repo`、`github_pages_url` 字段 |
| `data/config.json` | podcast 节点新增 GitHub 配置 |
| `data/config.example.json` | 同步 |
| `tests/test_podcast.py` | 新增 GitHubUploader 单元测试 |
| `tests/test_models_groups.py` | 新字段断言 |

### GitHubUploader 设计

```python
class GitHubUploader:
    """Upload files via GitHub Releases API."""

    def __init__(self, repo: str, pages_url: str):
        # repo: "Ming-321/horizon"
        # GITHUB_TOKEN from env

    async def create_release_and_upload(self, local_path: Path, tag: str) -> str:
        # 1. POST /repos/{repo}/releases  创建 release（tag=tag）
        # 2. POST {upload_url}  上传 asset
        # 返回 asset 下载 URL

    def get_feed_url(self) -> str:
        return f"{self.pages_url}/podcast/feed.xml"
```

### 集成修改

`_upload_and_update_feed` 逻辑：
1. 优先使用 `github_repo`（非空时）→ GitHubUploader
2. 回退到 `r2_bucket`（非空时）→ R2Uploader
3. 都为空 → 跳过

feed.xml 写入 `docs/podcast/feed.xml` 后通过 subprocess git commit + push 更新 Pages。

### 配置结构

```json
{
  "output": {
    "podcast": {
      "github_repo": "Ming-321/horizon",
      "github_pages_url": "https://ming-321.github.io/horizon"
    }
  }
}
```

### 验收标准

1. `uv run pytest -v` 全部通过
2. MP3 可通过 GitHub Release URL 下载
3. feed.xml 通过 Pages URL 访问
4. AntennaPod 可订阅并播放

---

## 实现计划

### Phase 0: GitHubUploader + 配置 + 测试

**涉及文件：**
- 修改: `src/models.py`（PodcastConfig 新增 2 个字段）
- 修改: `src/services/podcast.py`（新增 GitHubUploader 类）
- 修改: `data/config.json`（podcast 新增 github_repo + github_pages_url）
- 修改: `data/config.example.json`（同步）
- 修改: `tests/test_podcast.py`（GitHubUploader 单元测试）
- 修改: `tests/test_models_groups.py`（新字段断言）

**关键改动：**

`src/models.py` — PodcastConfig 新增：
```python
github_repo: str = ""
github_pages_url: str = ""
```

`src/services/podcast.py` — 新增 GitHubUploader：
```python
class GitHubUploader:
    GITHUB_API = "https://api.github.com"

    def __init__(self, repo: str, pages_url: str):
        self.repo = repo
        self.pages_url = pages_url.rstrip("/")
        self.token = os.environ.get("GITHUB_TOKEN", "")

    async def create_release_and_upload(self, local_path: Path, tag: str) -> str:
        """Create a GitHub release and upload MP3 as asset."""
        # httpx.AsyncClient POST /repos/{repo}/releases
        # httpx.AsyncClient POST upload_url with file content
        # return browser_download_url

    def get_feed_path(self) -> Path:
        """Return local path for feed.xml in docs/ directory."""
        return Path("docs/podcast/feed.xml")
```

**验证：**
在项目根目录执行 `uv run pytest tests/test_models_groups.py tests/test_podcast.py -v`，预期全部通过。

### Phase 1: 集成 — PodcastPipeline 使用 GitHubUploader + feed push

**涉及文件：**
- 修改: `src/services/podcast.py`（`_upload_and_update_feed` 切换逻辑）
- 修改: `tests/test_podcast.py`（集成测试）

**关键改动：**

`_upload_and_update_feed` 修改为：
```python
async def _upload_and_update_feed(self, audio_path: Path, date: str) -> None:
    try:
        if self.config.github_repo:
            gh = GitHubUploader(self.config.github_repo, self.config.github_pages_url)
            tag = f"podcast-{date}"
            audio_url = await gh.create_release_and_upload(audio_path, tag)
            # update feed.xml
            feed_path = gh.get_feed_path()
            # ... generate feed + write to docs/podcast/feed.xml
            # git add + commit + push (subprocess)
        elif self.config.r2_bucket:
            # existing R2 logic
    except Exception as e:
        logger.warning("podcast: upload/feed failed: %s", e)
```

**验证：**
在项目根目录执行 `uv run pytest -v`，预期全部通过。

## 状态
- [x] Phase 0: GitHubUploader + 配置 + 测试
- [x] Phase 1: 集成 — PodcastPipeline 使用 GitHubUploader + feed push
- [x] 验证与审阅
