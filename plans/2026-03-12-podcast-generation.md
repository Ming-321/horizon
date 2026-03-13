# 阶段三：播客生成
> 深度: Deep

## 背景

Horizon 已完成阶段一（核心架构升级）和阶段二（多渠道输出），具备分组评分、内容 enrichment、简洁/详细 HTML 报告、wxpusher 推送等能力。阶段三的目标是将高分新闻转化为 AI 播客——双人对话脚本 + TTS 语音合成，生成可收听的每日音频摘要。

当前状态：
- 代码中尚无任何播客相关实现
- DashScope API key 已配置（`DASHSCOPE_API_KEY`），同时支持 LLM（Qwen3.5-plus）和 TTS（CosyVoice）
- `_render_outputs()` 已是统一的多渠道输出编排入口，可直接插入 podcast 分支
- `data/prompts/` 的外置 prompt 机制可直接复用

范围限定：本阶段仅实现**本地 MP3 生成**。音频托管（HTTP 链接暴露）和 wxpusher 推送音频链接为后续增量，不在本阶段范围内。

## 参考

- **DashScope CosyVoice v3 Python SDK**：非流式调用 `SpeechSynthesizer(model, voice).call(text)` 返回 MP3 bytes，每次请求需重新实例化，单次最大 20000 字符
- **CosyVoice v3-flash 音色**：`longanyang`（男，阳光青年）、`longanhuan`（女，活力开朗），均支持中英文和 SSML
- **WebSocket 端点**：新加坡节点 `wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference`
- **podcastfy**（6k 星）：对话 prompt 设计参考，分段式 + 停顿标记
- **现有服务模式**：`WxPusherService`（构造注入 config + 单入口方法 + 内部异常处理 + 返回状态）

## 设计决策

### 1. 整体工作流

```
grouped_for_summary（高分新闻）
    ↓ 选题筛选（两级均衡：组间 → 组内子话题，每子话题取 top N 或前 30%）
    ↓
Qwen3.5-plus 生成对话脚本（JSON: {"script": [{speaker, text}, ...]}）
    ↓
CosyVoice v3-flash 分角色合成语音（非流式，逐段）
    ↓
FFmpeg 拼接音频段 → MP3
    ↓
保存到 data/podcasts/<date>.mp3
```

### 2. 模块设计

新增 `src/services/podcast.py`，包含三个组件类 + 一个编排类：

- **`ScriptGenerator`**：调用 Qwen 生成双人对话脚本
  - 输入：筛选后的高分条目列表 + 日期
  - 输出：`List[Dict]`（`[{"speaker": "A"|"B", "text": "..."}]`）
  - LLM 返回 JSON 对象 `{"script": [...]}` 以兼容 `json_object` 模式
  - 内置 `_validate_script()` 校验 speaker 合法性、非空、总字数范围
  - prompt 从 `data/prompts/podcast_dialogue.txt` 加载

- **`TTSSynthesizer`**：调用 CosyVoice 合成语音
  - 输入：对话脚本 + 音色映射 + TTS 配置
  - 输出：临时音频文件列表（MP3 格式）
  - 每段对话独立合成（`SpeechSynthesizer` 需每次重新实例化）
  - 单段超过 20000 字符时截断并记录 warning
  - 单段 TTS 失败时跳过该段并记录 warning，不中断整体流程
  - `DASHSCOPE_API_KEY` 缺失时抛异常（由 PodcastPipeline 捕获）

- **`AudioMerger`**：FFmpeg 拼接音频段
  - 输入：临时音频文件列表
  - 输出：最终 MP3 文件路径
  - 使用 `ffmpeg -f concat -safe 0 -c copy` 拼接（SDK 返回统一 MP3 编码）

- **`PodcastPipeline`**：串联以上三步
  - 构造注入 `PodcastConfig` + `AIClient` + `TopicClassifier`（可选）
  - 对外暴露 `async generate(grouped_items, date) -> Optional[Path]`
  - **两级均衡选题算法**（`_select_items`）：
    1. **组间均衡**：遍历每个 group（头条速递 / 关注动态 / GitHub 热榜）
    2. **组内均衡**（仅头条速递和 GitHub 热榜）：
       - 复用 `TopicClassifier.classify_group()` 获取子话题分类
       - 每个子话题按 `ai_score` 降序，取 `max(items_per_group, ceil(len * 0.3))` 条（至少 1 条）
       - 关注动态：无评分，直接取全部（通常条目少）
    3. 汇总所有选出的条目，按 `ai_score` 降序排序作为最终素材
  - 无可用条目时返回 `None`
  - 内部异常处理，失败返回 None，临时文件清理

### 3. 对话脚本设计

角色设定：
- **A（longanyang）**：资深科技记者，负责介绍新闻要点和深度分析
- **B（longanhuan）**：好奇听众，提问、回应、补充评论，帮助展开话题

对话结构（prompt 契约）：
1. 开场白（A 打招呼 + 今日主题概览）
2. 逐条新闻讨论（A 介绍 → B 提问 → A 解答 → B 评论/过渡），每条至少 2 轮
3. 结尾总结（B 总结 + A 展望）

输出格式约束：
- JSON 对象 `{"script": [{"speaker": "A"|"B", "text": "..."}]}`
- speaker 仅允许 `"A"` 或 `"B"`
- 总字数 1500-3000 字中文
- 每段 text 不超过 500 字

目标时长：5-8 分钟

### 4. 配置结构

放入 `OutputConfig` 下（与 brief/html 同级），保持配置层级一致：

```python
class PodcastConfig(BaseModel):
    enabled: bool = False
    tts_model: str = "cosyvoice-v3-flash"
    tts_endpoint: str = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
    voice_a: str = "longanyang"
    voice_b: str = "longanhuan"
    max_script_chars: int = 3000
    items_per_group: int = 3
    output_dir: str = "data/podcasts"
    prompt_file: str = "podcast_dialogue.txt"
```

`OutputConfig` 扩展：
```python
class OutputConfig(BaseModel):
    brief: BriefConfig = Field(default_factory=BriefConfig)
    html: HtmlConfig = Field(default_factory=HtmlConfig)
    podcast: PodcastConfig = Field(default_factory=PodcastConfig)
```

JSON 配置：
```json
{
  "output": {
    "brief": { ... },
    "html": { ... },
    "podcast": {
      "enabled": false,
      "tts_model": "cosyvoice-v3-flash",
      "tts_endpoint": "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference",
      "voice_a": "longanyang",
      "voice_b": "longanhuan",
      "max_script_chars": 3000,
      "items_per_group": 3,
      "output_dir": "data/podcasts",
      "prompt_file": "podcast_dialogue.txt"
    }
  }
}
```

### 5. 集成位置与使用模式

播客生成放在 `_render_outputs()` 中（HTML 生成之后），天然支持两种使用模式：

- **嵌入正常流程**：`uv run horizon --hours 24` 运行完整 pipeline，podcast.enabled 时自动生成
- **独立补充生成**：`uv run horizon --from-cache` 从今日缓存重新生成所有输出（含播客），跳过 fetch/score/enrich

两种模式共享 `_render_outputs()`，无需额外参数。

集成代码：
```python
if self.config.output.podcast.enabled:
    try:
        from .services.podcast import PodcastPipeline
        ai_client_podcast = create_ai_client(self.config.ai)
        pipeline = PodcastPipeline(self.config.output.podcast, ai_client_podcast)
        audio_path = await pipeline.generate(grouped_for_summary, today)
        if audio_path:
            logger.info("podcast: saved to %s", audio_path)
            self.console.print(f"\N{STUDIO MICROPHONE} Podcast saved to: {audio_path}\n")
    except Exception as e:
        logger.warning("podcast generation failed: %s", e)
        self.console.print(f"[yellow]Podcast generation failed: {e}[/yellow]\n")
```

### 6. 向后兼容

- 未配置 `podcast` 节点时，`PodcastConfig` 默认 `enabled=False`，不触发任何逻辑
- `podcast.enabled = false` 时完全不导入 dashscope 或调用 ffmpeg
- 未安装 ffmpeg 或缺失 DASHSCOPE_API_KEY 时，仅在 enabled 时报错，不影响 summary/html 主流程

### 7. 依赖

- `dashscope>=1.23.4`（CosyVoice TTS Python SDK）
- 系统依赖：`ffmpeg`（`apt install ffmpeg`）

### 8. 涉及文件

| 文件 | 改动类型 |
|------|---------|
| `src/services/podcast.py` | 新增 |
| `src/renderers/topic_classifier.py` | 复用（PodcastPipeline 导入用于组内子话题分类） |
| `src/models.py` | OutputConfig 扩展 PodcastConfig |
| `src/orchestrator.py` | _render_outputs 新增 podcast 分支 |
| `data/prompts/podcast_dialogue.txt` | 新增 |
| `data/config.json` | output 下新增 podcast 配置 |
| `data/config.example.json` | 同步更新 |
| `pyproject.toml` | 新增 dashscope 依赖 |
| `tests/test_podcast.py` | 新增 |

---

## 实现计划

### Phase 0: 基础设施 — 配置模型 + 依赖 + prompt 文件

**涉及文件：**
- 修改: `src/models.py`（新增 `PodcastConfig`，`OutputConfig` 增加 `podcast` 字段）
- 修改: `pyproject.toml`（新增 `dashscope>=1.23.4` 依赖）
- 修改: `data/config.json`（`output` 下新增 `podcast` 节点，`enabled: false`）
- 修改: `data/config.example.json`（同步新增 `podcast` 示例配置）
- 新增: `data/prompts/podcast_dialogue.txt`（对话脚本生成 prompt）
- 修改: `tests/test_models_groups.py`（新增 PodcastConfig 测试）

**关键改动：**

`src/models.py` 新增：
```python
class PodcastConfig(BaseModel):
    enabled: bool = False
    tts_model: str = "cosyvoice-v3-flash"
    tts_endpoint: str = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
    voice_a: str = "longanyang"
    voice_b: str = "longanhuan"
    max_script_chars: int = 3000
    items_per_group: int = 3
    output_dir: str = "data/podcasts"
    prompt_file: str = "podcast_dialogue.txt"
```

`OutputConfig` 扩展：
```python
class OutputConfig(BaseModel):
    brief: BriefConfig = Field(default_factory=BriefConfig)
    html: HtmlConfig = Field(default_factory=HtmlConfig)
    podcast: PodcastConfig = Field(default_factory=PodcastConfig)
```

`data/prompts/podcast_dialogue.txt` prompt 关键约束：
- 角色 A（科技记者）和 B（好奇听众）
- 三段式结构：开场 → 逐条讨论（每条至少 2 轮）→ 结尾
- 输出 JSON：`{"script": [{"speaker": "A"|"B", "text": "..."}]}`
- 总字数 1500-3000 字，每段 text ≤ 500 字

向后兼容验证：旧配置（无 `output.podcast`）仍能正常加载。

**验证：**
在项目根目录执行：
```bash
uv sync --extra dev && uv run pytest tests/test_models_groups.py -v
```
预期：
- 所有现有模型测试通过
- 新增测试验证 PodcastConfig 默认值正确
- 旧配置（缺少 podcast 节点）正常加载

### Phase 1: 核心模块 — ScriptGenerator + TTSSynthesizer + AudioMerger + PodcastPipeline

**涉及文件：**
- 新增: `src/services/podcast.py`（4 个类 + `_select_items` 选题方法）
- 复用: `src/renderers/topic_classifier.py`（`PodcastPipeline._select_items` 调用 `TopicClassifier.classify_group` 做组内子话题划分）
- 新增: `tests/test_podcast.py`（单元测试，含选题均衡性测试）

**关键改动：**

`src/services/podcast.py`：

```python
class ScriptGenerator:
    def __init__(self, ai_client: AIClient, prompt_text: str, max_chars: int):
        ...
    async def generate(self, items: List[ContentItem], date: str) -> List[Dict[str, str]]:
        """调用 LLM 生成对话脚本。
        LLM 返回 {"script": [...]}, 解析后返回列表。
        调用 _validate_script() 校验。"""

    @staticmethod
    def _validate_script(script: List[Dict]) -> bool:
        """校验：speaker 仅 A|B、text 非空、段数 >= 4、总字数在范围内"""
```

`ScriptGenerator` 解析逻辑（`AIClient.complete()` 返回 `CompletionResult(text, usage)`）：
```python
completion = await self.ai_client.complete(system=self.prompt_text, user=user_msg)
try:
    data = json.loads(completion.text)  # CompletionResult.text
    script = data["script"]  # 对象包裹，兼容 json_object 模式
except (json.JSONDecodeError, KeyError) as e:
    logger.warning("Failed to parse script JSON: %s", e)
    return []
if not self._validate_script(script):
    logger.warning("Script validation failed")
    return []
return script
```

```python
class TTSSynthesizer:
    def __init__(self, model: str, endpoint: str, voice_map: Dict[str, str]):
        """endpoint 从 PodcastConfig.tts_endpoint 传入。
        构造时设置 dashscope.base_websocket_api_url = endpoint。"""
        ...
    async def synthesize(self, script: List[Dict[str, str]], work_dir: Path) -> List[Path]:
        """逐段 TTS（阻塞式 SDK 调用通过 asyncio.to_thread 包装）。
        单段失败跳过并 warning。全部失败时返回空列表。"""

class AudioMerger:
    @staticmethod
    async def merge(segments: List[Path], output: Path) -> Path:
        """FFmpeg concat 拼接（-c copy，SDK 返回统一 MP3 编码）。
        需先确认 ffmpeg 可用，不可用时抛 FileNotFoundError。
        segments 为空时返回 None。"""

class PodcastPipeline:
    def __init__(self, config: PodcastConfig, ai_client: AIClient):
        """从 config 提取所有参数传给子组件：
        - prompt_file -> src.ai.prompts.load_prompt(config.prompt_file) -> ScriptGenerator
        - items_per_group -> 每个子话题的选题上限（与 30% 取较大值）
        - ai_client -> 内部构造 TopicClassifier(ai_client)，复用 HTML 分类逻辑
        - max_script_chars -> ScriptGenerator
        - tts_model/tts_endpoint/voice_a/voice_b -> TTSSynthesizer
        - output_dir -> 最终 MP3 输出目录（自动 mkdir）
        内部 _select_items() 调用 TopicClassifier.classify_group() 做组内子话题划分。"""
        ...
    async def generate(
        self, grouped_items: Dict[str, List[ContentItem]], date: str,
    ) -> Optional[Path]:
        """完整管道。无可用条目时返回 None。
        全部 segment 失败时返回 None（不调用 ffmpeg）。
        临时文件在 finally 中清理。
        播客脚本 LLM 复用 config.ai（与主 pipeline 相同模型配置）。"""
```

`tests/test_podcast.py`（mock AIClient 和 dashscope SDK）：
- `test_script_generator_parse`：验证 LLM 返回 `{"script": [...]}` 的解析
- `test_script_validation_rejects_bad_speaker`：speaker 非 A/B 时校验失败
- `test_tts_synthesizer_voice_mapping`：验证 speaker A/B 映射到正确的 voice
- `test_audio_merger_ffmpeg_command`：验证生成正确的 concat 列表和 ffmpeg 命令
- `test_pipeline_no_items_returns_none`：空输入返回 None
- `test_pipeline_exception_returns_none`：子组件异常时返回 None
- `test_select_items_balanced`：验证选题从多个子话题均衡选取，无单一子话题垄断

**验证：**
```bash
uv run pytest tests/test_podcast.py -v
```
预期：所有播客单元测试通过。

### Phase 2: 集成 — orchestrator 插桩 + StorageManager + 端到端测试

**涉及文件：**
- 修改: `src/orchestrator.py`（`_render_outputs()` 新增 podcast 分支）
- 修改: `src/storage/manager.py`（新增 `save_podcast()` 方法，与 `save_html()`/`save_brief()` 对齐）
- 修改: `tests/test_output_integration.py`（新增 podcast 集成断言）

**关键改动：**

`src/storage/manager.py` 新增：
```python
def save_podcast(self, date: str, audio_bytes: bytes) -> Path:
    """Save podcast MP3 file, consistent with save_html/save_brief pattern."""
    podcast_dir = self.data_dir / "podcasts"
    podcast_dir.mkdir(exist_ok=True)
    path = podcast_dir / f"horizon-{date}.mp3"
    path.write_bytes(audio_bytes)
    return path
```

在 `_render_outputs()` 方法末尾（HTML 生成之后）新增：
```python
if self.config.output.podcast.enabled:
    try:
        from .services.podcast import PodcastPipeline
        ai_client_podcast = create_ai_client(self.config.ai)
        pipeline = PodcastPipeline(self.config.output.podcast, ai_client_podcast)
        audio_path = await pipeline.generate(grouped_for_summary, today)
        if audio_path:
            logger.info("podcast: saved to %s", audio_path)
            self.console.print(f"\N{STUDIO MICROPHONE} Podcast saved to: {audio_path}\n")
        else:
            self.console.print("[yellow]Podcast generation returned no output[/yellow]\n")
    except Exception as e:
        logger.warning("podcast generation failed: %s", e)
        self.console.print(f"[yellow]Podcast generation failed: {e}[/yellow]\n")
```

**验证：**

自动化测试：
```bash
uv run pytest -v
```
预期：全部测试通过。

端到端测试（需先安装 ffmpeg：`sudo apt install -y ffmpeg`）：
```bash
ffmpeg -version
# 在 config.json 中设置 output.podcast.enabled = true
uv run horizon --from-cache
# 检查日志中出现 podcast 分支信息
# 检查输出文件
ls -la data/podcasts/
# 验证文件非空且可播放
file data/podcasts/*.mp3
```
预期：`data/podcasts/<date>.mp3` 文件存在、大小 > 0、格式为 MPEG audio。

### Phase 3: 验证与审阅

由 sub-agent 审阅：
- 代码质量：类职责清晰、异常处理完整、无硬编码
- 测试覆盖率：ScriptGenerator/TTSSynthesizer/AudioMerger/PodcastPipeline 均有单元测试
- 集成正确性：orchestrator 分支正确触发、配置向后兼容、podcast.enabled=false 无副作用

验证命令：
```bash
uv run pytest -v
```

## 状态
- [x] Phase 0: 基础设施 — 配置模型 + 依赖 + prompt 文件
- [x] Phase 1: 核心模块 — ScriptGenerator + TTSSynthesizer + AudioMerger + PodcastPipeline
- [x] Phase 2: 集成 — orchestrator 插桩 + 端到端测试
- [x] 验证与审阅
