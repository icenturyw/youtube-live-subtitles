# YouTube Live Subtitles (v2.0.0)

本项目是一个为 YouTube 视频（尤其是直播和超长视频）生成实时/准实时字幕的工具。支持本地 Whisper 模型识别、外部极速 API（Groq/OpenAI）识别，并提供云端 MongoDB 同步功能。

## ✨ 核心特性

- **多模式识别**：支持本地 Faster-Whisper、Groq API、OpenAI API 及浏览器内置 API。
- **现代化架构**：后端由 FastAPI 驱动，异步处理，性能卓越。
- **VPS/Docker 友好**：支持一键 Docker 部署，插件可远程连接 VPS。
- **智能长视频处理**：自动音频提取与动态压缩，突破 API 文件大小限制。
- **语义化断句**：针对中文优化的语义分割算法， subtitle 阅读更流畅。
- **云端同步**：适配 MongoDB，同一视频字幕在不同设备间瞬间同步。
- **极简 UI**：漂亮的透明磨砂感字幕容器，支持字体、颜色、位置自定义。

## 🔄 更新日志

### v2.0.0 (2026-01-17) - 现代架构升级 🚀

**新功能 (架构重构)**
- **FastAPI 核心驱动**：后端从传统的 `http.server` 迁移至性能强劲的 `FastAPI` 异步框架。
- **模块化代码结构**：逻辑拆分为 `api/`, `core/`, `db/`, `models/`。
- **VPS 远程部署支持**：支持 Docker 化一键部署，插件可配置任意远程服务器 IP。
- **安全鉴权系统**：新增 `API_AUTH_KEY` 验证机制。

**优化与修复**
- **鉴权头注入**：插件在通信时会自动注入 `X-API-Key` 请求头。
- **代码整洁度**：转录逻辑移至 `core` 模块，入口统一为 `main.py`。

## 🐳 Docker 部署指南 (VPS)

如果您想在远程服务器上部署识别服务：

1. **构建镜像**：
   ```bash
   cd whisper-server
   docker build -t whisper-backend .
   ```

2. **运行容器**：
   ```bash
   docker run -d \
     -p 8765:8765 \
     --name whisper-server \
     -e API_AUTH_KEY="your-custom-auth-key" \
     -e MONGO_URI="your-mongodb-uri" \
     whisper-backend
   ```

3. **插件配置**：
   - 打开扩展设置，将 **服务器地址** 改为 `http://your-vps-ip:8765`。
   - 在 **服务器鉴权 Key** 中填入您设置的 `API_AUTH_KEY`。

## 🛠️ 环境要求

- **Python 3.10+** / **Docker**
- **FFmpeg** (用于音频预处理)

## 🚀 安装与启动

### 本地模式
1. 进入 `whisper-server`。
2. 运行 `start.bat`。

### 开发者说明
可以直接使用 uvicorn 启动：
```bash
uvicorn main:app --reload --port 8765
```

## ⚖️ 许可证

MIT License