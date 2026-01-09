# YouTube 离线 Whisper 字幕生成器 (Local Whisper Subtitles)

一个强大的 Chrome 扩展，结合 Python 后端，利用 **Faster-Whisper** 模型为 YouTube 视频生成高质量的本地离线字幕。

## 🌟 核心功能

- 🧠 **纯本地识别** - 使用 `faster-whisper` 模型在本地 CPU 运行，无需 OpenAI API Key，无需联网发送音频。
- �🇳 **简体中文优化** - 自动将繁体中文识别结果转换为简体中文（集成 OpenCC）。
- ✂️ **智能字幕切分** - 自动拆分冗长段落，确保字幕显示短促且易于阅读。
- 💾 **SRT 导出** - 支持一键下载生成的字幕为标准 SRT 格式。
- ⚡ **性能加速** - 针对 CPU 多核进行优化，支持并行处理和多种量化模式。
- 🔒 **隐私安全** - 音频下载、处理和存储完全在您的本地设备上完成。

## 🛠️ 环境要求

- **Python 3.10+**
- **FFmpeg** (需添加到系统 PATH)
- **Chrome 浏览器**

## 🚀 安装步骤

### 1. 部署后端服务
1. 进入 `whisper-server` 文件夹。
2. 运行 `start.bat`。它会自动创建虚拟环境并安装所需依赖（包括 `faster-whisper`, `yt-dlp`, `opencc` 等）。
3. 首次启动会下载指定的 Whisper 模型（默认 `base` 版，约 150MB）。
4. 看到 `服务地址: http://127.0.0.1:8765` 说明启动成功。

### 2. 安装浏览器扩展
1. 打开 Chrome 浏览器，访问 `chrome://extensions/`。
2. 开启右上角的 **开发者模式**。
3. 点击 **加载已解压的扩展程序**。
4. 选择本项目根目录下的 `youtube-live-subtitles` 文件夹。

## 📖 使用说明

1. 打开任意 YouTube 视频页面。
2. 点击扩展图标，确保状态显示为“就绪”。
3. 在设置中选择识别语言（或点击“自动检测”）。
4. 点击 **生成字幕**。
5. 后端后台会开始下载视频音频并进行本地识别，您可以实时在插件面板看到进度。
6. 完成后，字幕会自动显示在视频播放器上方。
7. 点击 **下载 SRT** 可保存字幕文件。

## ⚙️ 模型配置

您可以根据电脑配置在 `whisper-server/server.py` 中修改 `MODEL_SIZE`：
- `tiny`: 识别速度极快，占用资源极低。
- `base` (默认): 速度与准确度的最佳平衡。
- `small`: 准确度更高，适合性能较好的电脑。

## 📦 技术栈

- **Frontend**: Manifest V3, Vanilla JS, CSS3
- **Backend**: Python, FastAPI, Faster-Whisper, OpenCC
- **Tools**: yt-dlp, FFmpeg

## ⚖️ 许可证

MIT License
