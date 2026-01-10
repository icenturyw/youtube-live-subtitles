# YouTube 本地 Whisper 字幕生成器 (Local Whisper Subtitles)

一个强大的 Chrome 扩展，结合 Python 后端，利用 **Faster-Whisper** 模型为 YouTube 视频生成高质量的本地字幕，并支持通过 **MongoDB** 实现云端同步共享。

## 🌟 核心功能

- 🧠 **纯本地识别** - 使用 `faster-whisper` 模型在本地 GPU/CPU 运行，无需 OpenAI API Key。
- ☁️ **云端同步缓存** - 集成 MongoDB，一次生成，多设备共享。在任何一台设备上生成字幕后，其他设备再访问同一视频可直接从云端下载，无需重复计算。
- 🇨🇳 **简体中文优化** - 自动将繁体中文识别结果转换为简体中文（集成 OpenCC）。
- ✂️ **智能字幕切分** - 自动拆分冗长段落，确保字幕显示短促且易于阅读。
- 💾 **SRT 导出** - 支持一键下载生成的字幕为标准 SRT 格式。
- ⚡ **性能加速** - 针对 GPU/CPU 多核进行优化，支持并行处理和多种量化模式。
- 🔒 **隐私安全** - 音频下载和识别在您本地完成，字幕数据存储在您的私有云数据库中。

## ☁️ 云同步配置 (MongoDB Atlas)

本项目支持连接到 MongoDB 云数据库，实现字幕的跨设备共享。推荐使用 **MongoDB Atlas** 的永久免费层。

### 配置方式

**方式一：环境变量（推荐）**

创建 `whisper-server/.env` 文件或设置系统环境变量：

```bash
MONGO_URI=mongodb+srv://<user>:<password>@cluster.mongodb.net/?retryWrites=true&w=majority
MONGO_DB_NAME=youtube_subtitles
MONGO_COLLECTION_NAME=videos
```

**方式二：直接修改配置文件**

编辑 `whisper-server/server.py` 中的配置变量。

### MongoDB Atlas 设置步骤

1. 访问 [MongoDB Atlas 官网](https://www.mongodb.com/cloud/atlas/register) 注册并创建一个 **M0 (Free)** 共享集群。
2. 在 **Database Access** 页面创建一个数据库用户，记下用户名和密码。
3. 在 **Network Access** 页面，添加 `0.0.0.0/0` 允许所有 IP 连接（或仅添加您自己的 IP）。
4. 在集群主页点击 `Connect` -> `Drivers`，选择 Python，获取以 `mongodb+srv://...` 开头的连接字符串。

如果 `MONGO_URI` 留空或连接失败，服务会自动回退到纯本地缓存模式。

## 🛠️ 环境要求

- **Python 3.10+**
- **FFmpeg** (需添加到系统 PATH)
- **Chrome 浏览器**

## 🚀 安装步骤

### 1. 部署后端服务
1. 进入 `whisper-server` 文件夹。
2. 运行 `start.bat`。它会自动创建虚拟环境并安装所需依赖（包括 `faster-whisper`, `yt-dlp`, `pymongo` 等）。
3. 首次启动会下载指定的 Whisper 模型（默认 `large-v3`）。
4. （可选）根据上方指引配置好 MongoDB 以开启云同步。
5. 看到 `服务地址: http://127.0.0.1:8765` 和 `[MongoDB] 连接成功` 字样说明启动成功。

### 2. 安装浏览器扩展
1. 打开 Chrome 浏览器，访问 `chrome://extensions/`。
2. 开启右上角的 **开发者模式**。
3. 点击 **加载已解压的扩展程序**。
4. 选择本项目根目录。

## 📖 使用说明

1. 打开任意 YouTube 视频页面。
2. 点击扩展图标，确保状态显示为"Whisper 服务已就绪"。
3. 在设置中选择识别语言（或选择"自动检测"）。
4. 点击 **生成字幕**。
5. 如果云端已有缓存，将直接加载。否则，后端会开始本地识别，并实时推送进度。
6. 完成后，字幕会自动显示在视频播放器上方，并上传至云端。
7. 点击 **下载 SRT** 可保存字幕文件。

## ⚙️ 模型配置

您可以根据电脑配置在 `whisper-server/server.py` 中修改 `MODEL_SIZE`：
- `tiny`, `base`: 识别速度快，适合 CPU 运行或追求实时性。
- `small`, `medium`: 准确度更高，推荐在 GPU 上使用。
- `large-v3` (默认): 准确度最高，尤其适合多语言场景，强烈建议在 GPU (CUDA) 环境下使用。

## 📦 技术栈

- **Frontend**: Manifest V3, Vanilla JS, CSS3
- **Backend**: Python, HTTP Server, Faster-Whisper, OpenCC, MongoDB (pymongo)
- **Tools**: yt-dlp, FFmpeg

## 🔄 更新日志

### v1.0.1 (2026-01-10)

**安全性改进**
- MongoDB 凭证现在支持从环境变量读取，提升安全性
- 添加了 `.env.example` 示例配置文件
- 更新了 `.gitignore`，排除敏感配置文件

**兼容性修复**
- 修复了 Chrome Private Network Access (PNA) 策略导致的 CORS 错误
- 扩展现在通过 background script 代理本地服务请求
- 添加了 `Access-Control-Allow-Private-Network` 响应头

**健壮性增强**
- 为服务检查添加了 3 秒超时，避免请求无限等待
- 改进了错误处理和日志记录
- 修复了可能导致 Promise 多次 resolve/reject 的竞态条件
- 移除了未使用的浏览器扩展权限

## ⚖️ 许可证

MIT License