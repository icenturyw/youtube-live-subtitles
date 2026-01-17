# YouTube 本地 Whisper 字幕生成器 (Local Whisper Subtitles)

一个强大的 Chrome 扩展，结合 Python 后端，利用 **Faster-Whisper** 模型或 **Groq/OpenAI API** 为 YouTube 视频生成高质量字幕，并支持通过 **MongoDB** 实现云端同步共享。

## 🌟 核心功能

- 🧠 **多模式识别** - 支持 `faster-whisper` 本地高效识别（无需 API）及 **Groq API** 极速识别（毫秒级响应）。
- ☁️ **云端同步缓存** - 集成 MongoDB，一次生成，多设备共享。即使使用 API 识别的结果也会同步到云端。
- 🇨🇳 **简体中文优化** - 自动将繁体中文识别结果转换为简体中文（集成 OpenCC）。
- ✂️ **智能字幕切分** - 自动拆分冗长段落，确保字幕显示短促且易于阅读。
- 📉 **自动音频压缩** - 针对长视频自动进行音频重采样和压缩，确保文件符合 API 上传限制（如 Groq 的 25MB 限制）。
- 💾 **SRT 导出** - 支持一键下载生成的字幕为标准 SRT 格式。
- 🔒 **隐私与性能** - 支持本地私有化部署，优先保护用户隐私，同时针对 GPU/CPU 进行多核优化。

## ⚙️ 配置说明

### 1. 识别服务配置
插件支持以下几种识别模式，可在插件设置面板中切换：
- **本地 Whisper**: 完全本地运行，适合隐私要求高、显卡性能强的场景。
- **Groq API (推荐)**: 极速识别，适合大长视频或追求体验的用户。
- **OpenAI API**: 官方主流 API 识别模式。
- **浏览器内置**: 备用方案。

### 2. 环境变量配置 (.env)
在 `whisper-server/.env` 中配置以下信息以启用高级功能：

```bash
# MongoDB 配置
MONGO_URI=mongodb+srv://<user>:<password>@cluster.mongodb.net/
MONGO_DB_NAME=youtube_subtitles

# API Key 配置 (可选，也可在插件界面手动配置)
GROQ_API_KEY=gsk_xxxx
OPENAI_API_KEY=sk_xxxx
```

### 3. 静默后台模式 (推荐)
如果您不想每次手动启动黑色的控制台窗口，可以使用以下方式：
- **手动后台启动**：双击 `whisper-server/run_hidden.vbs`，服务将静默运行。
- **配置开机自启**：双击 `whisper-server/setup_autostart.bat`，之后每次登录 Windows 系统服务都会自动启动。
- **查看日志**：如果服务运行异常，请查看 `whisper-server/server.log`。

## 🛠️ 环境要求

- **Python 3.10+** (建议安装时勾选 "Add Python to PATH")
- **FFmpeg** (必备，用于音频提取和压缩)
- **Chrome 浏览器**

## 🚀 安装步骤

### 1. 部署后端服务
1. 进入 `whisper-server` 文件夹。
2. 运行 `start.bat` 完成初始化安装。
3. 如果您只使用 Groq/OpenAI API，可以不安装大型模型依赖，此时后端内存占用极低。
4. **强烈建议**：运行 `setup_autostart.bat` 配置开机自启，之后即可彻底忘记后端窗口。

### 2. 安装浏览器扩展
1. 打开 Chrome `chrome://extensions/`。
2. 开启 **开发者模式**。
3. 点击 **加载已解压的扩展程序**，选择本项目根目录。

## 📖 使用建议

1. **首次使用**：建议先连接 MongoDB 以获得最佳的跨设备体验。
2. **极速体验**：在设置中选择 **Groq API** 并配置 API Key，点击 **保存配置信息**。
3. **长视频处理**：系统会自动处理长视频压缩，请耐心等待“上传并识别”进度条走完。

## 🔄 更新日志

### v1.1.0 (2026-01-17)

**新功能**
- **Groq/OpenAI API 支持**：集成极速识别接口，识别速度提升 10 倍以上。
- **配置持久化**：新增手动“保存配置信息”按钮，支持永久保存选中的识别服务和 API Key。
- **智能压缩引擎**：新增 `ffmpeg` 自动压缩逻辑，完美解决超长视频导致的 API 413 (Entity Too Large) 错误。
- **批量生成升级**：播放列表批量生成功能现在同样支持 Groq 和 OpenAI API，大幅缩短排队等待时间。

**修复与优化**
- **UI 增强**：进度条新增具体百分比显示，进度反馈更精细。
- **稳定性修复**：修复了扩展重载导致的 `Context invalidated` 错误及部分 DOM 元素缺失引发的 JS 崩溃。
- **同步优化**：API 识别结果现在同样可以精准同步至 MongoDB 云端。

## ⚖️ 许可证

MIT License