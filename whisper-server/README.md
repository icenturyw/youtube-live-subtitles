# YouTube Whisper 字幕服务

使用 OpenAI Whisper 本地模型为 YouTube 视频生成高精度字幕的后端服务。

## 功能特点

- 🎯 **高精度识别** - 使用 OpenAI Whisper / SenseVoice 模型
- 🌍 **多语言支持** - 自动检测或指定语言，支持多种专业领域 Prompt
- 💾 **二级缓存系统** - 支持原始转录 (raw) 与最终结果 (final) 的分离缓存，更改翻译设置无须重复转录
- 🤖 **VideoLingo 翻译流水线** - 移植 VideoLingo 核心逻辑，支持“摘要提取 -> 意译校对 -> 润色优化”多级翻译
- 📏 **行数一致性保证** - 自动校验翻译行数并支持智能重试，解决本地 LLM 合并行的问题
- ⚡ **异步处理** - 基于队列的后台任务系统，支持播放列表批量转录
- 📡 **实时性能监控** - 详细的步骤耗时日志，任务状态全程追踪，并持久化到 `server.log`
- 🗄️ **PostgreSQL 云同步** - 使用统一的 PostgreSQL 数据库 (83.229.124.177) 实现多端同步
- 🔄 **自动补全同步** - 服务启动时自动检测本地 cache 并补全到云端数据库
- 🛡️ **健壮性增强** - 自动错误重试（下载、API 调用），集成 `json-repair` 及强健的异常连接池池化防断机制
- 🧹 **内存防爆机制** - 挂载死缓存定时清理线程，防止长时大规模播放列表识别导致的 OOM (Out of Memory)
- 🔒 **并发 IO 安全** - 基于 UUID 洗牌临时分割音频切片，规避了高并发下 FFmpeg 的锁死和越权覆盖缺陷
## 系统要求

- Python 3.10+
- FFmpeg（用于音频处理）
- CUDA（可选，GPU 加速）

## 安装步骤

### 1. 安装 FFmpeg

**Windows:**
1. 从 https://www.gyan.dev/ffmpeg/builds/ 下载
2. 解压到 `C:\ffmpeg`
3. 添加 `C:\ffmpeg\bin` 到系统 PATH

**或使用 Chocolatey:**
```bash
choco install ffmpeg
```

### 2. 安装依赖

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境 (Windows)
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 启动服务

**Windows - 双击运行:**
```
start.bat
```

**或手动启动:**
```bash
python server.py
```

服务将在 http://127.0.0.1:8765 启动

## API 接口

### 健康检查
```
GET /
```

### 开始转录
```
POST /transcribe
Content-Type: application/json

{
  "video_url": "https://www.youtube.com/watch?v=xxxxx",
  "language": "zh"  // 可选，不填则自动检测
}
```

### 获取状态/结果
```
GET /task/{task_id}
```

### 获取视频缓存
```
GET /status/{video_id}
```

### 播放列表转录
```
POST /transcribe_playlist
Content-Type: application/json

{
  "playlist_url": "https://www.youtube.com/playlist?list=xxxxx",
  "service": "local",
  "engine": "whisper"
}
```

### 删除缓存
```
DELETE /cache/{video_id}
```

## Whisper 模型选择

在 `main.py` 或 `core/whisper_engine.py` 中修改模型配置。

| 模型 | 大小 | 速度 | 精度 |
|------|------|------|------|
| tiny | 39M | 最快 | 一般 |
| base | 74M | 快 | 较好 |
| small | 244M | 中等 | 好 |
| medium | 769M | 慢 | 很好 |
| large | 1550M | 最慢 | 最好 |

建议：
- CPU: 使用 `tiny` 或 `base`
- GPU: 可使用 `small` 或 `medium`

## 目录结构

```
whisper-server/
├── server.py         # 主服务
├── requirements.txt  # 依赖
├── start.bat         # 启动脚本
├── cache/            # 字幕缓存
└── README.md
```

## 常见问题

### Q: 首次启动很慢？
A: 首次启动会下载 Whisper 模型（约 150MB），请耐心等待。

### Q: 下载视频时总是报 "Sign in to confirm you're not a bot" 错误？
A: YouTube 最近的的反爬风控升级。请使用 Edge 或 Chrome 下载 `Get cookies.txt LOCALLY` 插件，在 YouTube 登录状态下点击导出。将得到的文件重命名为 `cookies.txt`，并放在 `whisper-server/` 这个包含 `server.py` 的目录下即可。

### Q: 识别不准确？
A: 尝试使用更大的模型（如 `small` 或 `medium`）。

### Q: 启动流程是怎样的？
A: 推荐运行 `python main.py`。启动时系统会自动连接 PostgreSQL 并开启后台线程同步本地 `cache/` 目录下的所有 JSON 文件，确保云端数据是最新的。

## 许可证

MIT License
