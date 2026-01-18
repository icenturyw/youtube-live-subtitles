# YouTube Live Subtitles (v2.2.1)

本项目是一个为 YouTube 视频（尤其是直播和超长视频）生成实时/准实时字幕的工具。支持本地 GPU 加速识别、LLM 智能纠错、多端 Supabase 云同步功能。

## ✨ 核心特性

- **🚀 满血 GPU 加速**：深度适配 `faster-whisper`，支持 CUDA 12.x 自动检测，识别速度提升 5-10 倍。
- **🧠 LLM 智能纠错**：支持接入本地 LM Studio，自动修正识别过程中的错别字、谐音词，提升字幕质量。
- **❄️ 翻译效率优化**：智能检测源语言与目标语言，相同时自动跳过翻译步骤，大幅节省 API 费用与时间。
- **☁️ Supabase 云同步**：摒弃不稳定的 MongoDB，改用 Supabase (Postgres) 实现极速多端字幕共享。
- **🎨 增强型 UI**：全新的折叠面板设计，保持界面简洁；新增 LLM 纠错一键开关。
- **语义化断句**：针对中文优化的语义分割算法，阅读逻辑更清晰。
- **多模式识别**：支持本地 Whisper、SenseVoice、Groq API 及 OpenAI API。

## 🔄 最新更新

### v2.2.1 (2026-01-18) - 鲁棒性与体验双重进化 🛠️
- **时间轴精度重构**：重新设计单词对齐算法，彻底解决长句拆分后的 00:00:00 时间轴异常及乱序问题。
- **翻译逻辑优化**：新增“同语言跳过”逻辑。若源语言与目标语言一致，且关闭纠错或已使用本地纠错，则跳过云端 LLM 翻译。
- **缓存逻辑细化**：点击“清除缓存”时保持已下载音频文件，仅清除字幕 JSON (包含 raw 缓存) 及数据库记录，极大提升重试效率。
- **稳定性修复**：修复 `spaCy` 语义分割时的递归过深导致的崩溃问题；增加了中英文 spaCy 模型的自动加载与健壮性校验。

### v2.2.0 (2026-01-18) - 存储进化与性能巅峰 ⚡
- **云同步迁移**：全面支持 **Supabase**，解决 MongoDB 连接不稳问题。
- **硬件自适应**：智能检测 GPU/CPU 环境，自动切换 `float16` 精度和 `large-v3-turbo` 顶级模型。
- **UI 交互优化**：设置项支持折叠，新增 LLM 纠错全局开关。
- **批量处理增强**：修复播放列表批量转录接口，支持多任务并行排队。

## 🚀 快速启动

### 识别服务 (后端)
1. 进入 `whisper-server` 目录。
2. 配置 `.env` 文件（参照示例填入 Supabase 密钥和 LM Studio 地址）。
3. 运行 `start.bat`。

### 浏览器插件 (前端)
1. 在开发者模式下加载本项目根目录。
2. 点击插件图标，配置您的服务器地址和连接密钥。

## ⚠️ Supabase 初始化
在使用云同步前，请在 Supabase SQL Editor 中运行以下脚本：
```sql
create table if not exists subtitles (
  video_id text primary key,
  language text,
  service text,
  domain text,
  engine text,
  subtitles jsonb,
  created_at timestamptz default now()
);
```

## 🛠️ 环境要求
- **Python 3.10+** (建议 3.13)
- **CUDA 12.x** (可选，用于 GPU 加速)
- **FFmpeg**

## ⚖️ 许可证
MIT License