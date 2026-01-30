# YouTube Live Subtitles (v2.2.2)

本项目是一个为 YouTube 视频（尤其是直播和超长视频）生成实时/准实时字幕的工具。支持本地 GPU 加速识别、LLM 智能纠错、多端 Supabase 云同步功能。

## ✨ 核心特性

- **🚀 Cloudflare Workers AI 加速**：深度集成 Cloudflare `whisper-large-v3-turbo` 模型。通过智能分片与 Base64 协议优化，完美绕过 25MB 限制，支持长达数小时的超长视频识别。
- **🚀 混合强强制对齐 (Forced Alignment)**：国内首创混合架构。Groq API 获取高质量文本 + 本地轻量级模型定位单词级时间轴，兼顾快、准、稳。
- **🚀 满血 GPU 加速**：深度适配 `faster-whisper`，支持 CUDA 12.x 自动检测，识别速度提升 5-10 倍。
- **🧠 LLM 智能纠错**：支持接入本地 LM Studio，自动修正识别过程中的错别字、谐音词，提升字幕质量。
- **❄️ 翻译效率优化**：智能检测源语言与目标语言，相同时自动跳过翻译步骤，大幅节省 API 费用与时间。
- **☁️ PostgreSQL 云同步**：完全切换至高性能 PostgreSQL (83.229.124.177) 实现稳健的多端字幕共享。
- **🎨 增强型 UI**：全新的折叠面板设计，保持界面简洁；新增 LLM 纠错一键开关。
- **语义化断句**：针对中文优化的语义分割算法，阅读逻辑更清晰。

## 🔄 最新更新

### v2.3.0 (2026-01-30) - 数据库架构统一 🗄️
- **PostgreSQL 核心化**：彻底移除 MongoDB 和 Supabase 支持，统一使用 `83.229.124.177` 数据库。
- **自动增量同步**：服务启动时自动扫描本地 `cache/` 并将缺失数据同步到云端 PostgreSQL。
- **日志持久化**：FastAPI 后端现在会同步将所有日志保存到 `server.log`，方便追溯。

### v2.2.3 (2026-01-23) - 云识别进化，打破边界 ☁️
- **Cloudflare Workers AI (V3-Turbo)**：支持使用 Cloudflare 全球加速网络进行语音识别，采用官方最新的 Base64 JSON 协议，响应极速且高度兼容。
- **智能分片转录逻辑**：针对 Cloudflare 25MB 的 Payload 限制，开发了自动音频压缩与“5分钟一片”的切割逻辑。长视频会自动切分、独立转录并自动合并时间轴，彻底解决 413 报错。
- **UI 适配**：插件前端新增 Cloudflare 服务选项，支持 API Token 校验与服务端状态实时同步。
- **Bug 修复**：解决了 Cloudflare 模式误触发浏览器内置 Web Speech API（需要播放视频）的逻辑漏洞。

### v2.2.2 (2026-01-19) - 混合动力，精准降临 ⚡
- **本地强制对齐 (Option 2)**：解决了 Groq API 在高性能模式下不稳定的问题。现在 Groq 负责文字识别，本地模型负责毫秒级对齐，长句拆分更贴合发音。
- **插件 UI 容错增强**：修复了报错状态下不显示刷新按钮的 Bug；后端实现了“空结果不入库”机制，彻底告别脏缓存死循环。
- **语言代码智能映射**：自动转换 Groq 返回的语言名称（如 Chinese）为本地模型可识别的代码（如 zh），对齐过程不再报错。

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
2. 配置 `.env` 文件（填入有效的 PostgreSQL `83.229.124.177` 数据库连接信息）。
3. 运行 `start.bat` (或直接运行 `python main.py`)。

### 浏览器插件 (前端)
1. 在开发者模式下加载本项目根目录。
2. 点击插件图标，配置您的服务器地址和连接密钥。

## 🗄️ PostgreSQL 表结构
在使用云同步前，请确保在数据库中创建以下表：
```sql
CREATE TABLE IF NOT EXISTS subtitles (
    id SERIAL PRIMARY KEY,
    video_id VARCHAR(255) UNIQUE NOT NULL,
    language VARCHAR(50),
    target_lang VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    subtitles JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_video_id ON subtitles(video_id);
```

## 🛠️ 环境要求
- **Python 3.10+** (建议 3.13)
- **CUDA 12.x** (可选，用于 GPU 加速)
- **FFmpeg**

## ⚖️ 许可证
MIT License