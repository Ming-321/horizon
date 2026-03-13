# data/

配置、提示词模板和生成输出目录。

## 结构

| 路径 | 用途 | 可修改 |
|------|------|--------|
| `config.json` | 主配置（信息源、AI、分组、输出） | ✅ 改结构需先确认 |
| `config.example.json` | 配置模板 | ✅ 随 config.json 同步 |
| `prompts/*.txt` | 播客对话、丰富等提示词 | ✅ 需先确认 |
| `templates/` | Jinja2 HTML 模板 | ✅ |
| `summaries/` | 生成的 Markdown 日报 | ❌ 自动生成 |
| `html/` | 生成的 HTML 报告 | ❌ 自动生成 |
| `podcasts/` | 生成的播客 MP3 | ❌ 自动生成 |
| `cache/` | 抓取缓存 | ❌ 临时文件 |
| `logs/` | 运行日志 | ❌ 自动生成 |
