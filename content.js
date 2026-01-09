// YouTube 字幕生成器 - Content Script（支持本地 Whisper 服务）
(function () {
    'use strict';

    // ============ 配置 ============
    const WHISPER_SERVER = 'http://127.0.0.1:8765';

    // ============ 状态变量 ============
    let subtitles = [];
    let subtitleContainer = null;
    let isVisible = true;
    let settings = {
        fontSize: 24,
        position: 'bottom'
    };
    let videoElement = null;
    let isProcessing = false;
    let currentTaskId = null;

    // ============ 初始化 ============
    function init() {
        findVideoElement();
        setupVideoListeners();
        console.log('YouTube 字幕生成器已加载');
    }

    function findVideoElement() {
        videoElement = document.querySelector('video.html5-main-video') ||
            document.querySelector('video');
        return videoElement;
    }

    function setupVideoListeners() {
        if (!videoElement) {
            const observer = new MutationObserver(() => {
                if (findVideoElement()) {
                    observer.disconnect();
                    attachVideoEvents();
                }
            });
            observer.observe(document.body, { childList: true, subtree: true });
        } else {
            attachVideoEvents();
        }
    }

    function attachVideoEvents() {
        if (!videoElement) return;
        videoElement.addEventListener('timeupdate', onTimeUpdate);
        videoElement.addEventListener('seeking', onSeeking);
        videoElement.addEventListener('seeked', onSeeked);
        console.log('视频事件监听器已绑定');
    }

    // ============ 字幕同步逻辑 ============
    function onTimeUpdate() {
        if (!subtitles.length || !isVisible) return;

        const currentTime = videoElement.currentTime;
        const subtitle = findSubtitleAtTime(currentTime);

        if (subtitle) {
            showSubtitle(subtitle.text);
        } else {
            showSubtitle('');
        }
    }

    // 二分查找字幕
    function findSubtitleAtTime(time) {
        let left = 0;
        let right = subtitles.length - 1;

        while (left <= right) {
            const mid = Math.floor((left + right) / 2);
            const sub = subtitles[mid];

            if (time >= sub.start && time <= sub.end) {
                return sub;
            } else if (time < sub.start) {
                right = mid - 1;
            } else {
                left = mid + 1;
            }
        }

        return null;
    }

    function onSeeking() {
        // 跳转时可添加过渡效果
    }

    function onSeeked() {
        onTimeUpdate();
    }

    // ============ 字幕容器 ============
    function createSubtitleContainer() {
        removeSubtitleContainer();

        subtitleContainer = document.createElement('div');
        subtitleContainer.id = 'yt-custom-subtitle-container';

        const textElement = document.createElement('div');
        textElement.id = 'yt-custom-subtitle-text';
        subtitleContainer.appendChild(textElement);

        updateSubtitleStyle();

        const player = document.querySelector('#movie_player') ||
            document.querySelector('.html5-video-player');

        if (player) {
            player.style.position = 'relative';
            player.appendChild(subtitleContainer);
        } else {
            document.body.appendChild(subtitleContainer);
        }

        console.log('字幕容器已创建');
    }

    function removeSubtitleContainer() {
        const existing = document.getElementById('yt-custom-subtitle-container');
        if (existing) existing.remove();
        subtitleContainer = null;
    }

    function updateSubtitleStyle() {
        if (!subtitleContainer) return;

        const positionStyles = settings.position === 'top'
            ? 'top: 60px; bottom: auto;'
            : 'bottom: 80px; top: auto;';

        subtitleContainer.style.cssText = `
      position: absolute;
      left: 50%;
      transform: translateX(-50%);
      ${positionStyles}
      z-index: 9999;
      max-width: 90%;
      text-align: center;
      pointer-events: none;
      transition: opacity 0.2s ease;
      opacity: ${isVisible ? 1 : 0};
    `;

        const textElement = document.getElementById('yt-custom-subtitle-text');
        if (textElement) {
            textElement.style.cssText = `
        display: inline-block;
        padding: 10px 20px;
        background: rgba(0, 0, 0, 0.85);
        color: white;
        font-size: ${settings.fontSize}px;
        font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'Noto Sans SC', 'Microsoft YaHei', sans-serif;
        border-radius: 8px;
        text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.9);
        line-height: 1.5;
        max-width: 100%;
        word-wrap: break-word;
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4);
      `;
        }
    }

    function showSubtitle(text) {
        if (!subtitleContainer) {
            if (text) createSubtitleContainer();
            else return;
        }

        const textElement = document.getElementById('yt-custom-subtitle-text');
        if (textElement) {
            textElement.textContent = text;
            textElement.style.display = text ? 'inline-block' : 'none';
        }
    }

    // ============ Whisper 服务交互 ============
    async function checkWhisperService() {
        try {
            const response = await fetch(`${WHISPER_SERVER}/`, {
                method: 'GET',
                mode: 'cors'
            });
            return response.ok;
        } catch (e) {
            return false;
        }
    }

    async function startWhisperTranscription(videoUrl, language, apiKey) {
        const response = await fetch(`${WHISPER_SERVER}/transcribe`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                video_url: videoUrl,
                language: language === 'auto' ? null : language,
                api_key: apiKey
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || '请求失败');
        }

        return await response.json();
    }

    async function pollTaskStatus(taskId) {
        // 使用 SSE 获取实时状态
        return new Promise((resolve, reject) => {
            const eventSource = new EventSource(`${WHISPER_SERVER}/stream/${taskId}`);

            eventSource.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

                    // 发送进度更新
                    sendProgress(data.progress, data.message);

                    if (data.status === 'completed') {
                        eventSource.close();
                        resolve(data);
                    } else if (data.status === 'error') {
                        eventSource.close();
                        reject(new Error(data.message));
                    }
                } catch (e) {
                    console.error('解析状态失败:', e);
                }
            };

            eventSource.onerror = () => {
                eventSource.close();
                // 降级到轮询
                fallbackPolling(taskId).then(resolve).catch(reject);
            };
        });
    }

    async function fallbackPolling(taskId) {
        const maxAttempts = 3600;  // 最多等待 30 分钟 (1800 秒)

        for (let i = 0; i < maxAttempts; i++) {
            await sleep(500);

            try {
                const response = await fetch(`${WHISPER_SERVER}/status/${taskId}`);
                const data = await response.json();

                sendProgress(data.progress, data.message);

                if (data.status === 'completed') {
                    return data;
                } else if (data.status === 'error') {
                    throw new Error(data.message);
                }
            } catch (e) {
                if (i === maxAttempts - 1) throw e;
            }
        }

        throw new Error('识别超时 (已超过 30 分钟)');
    }

    function sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    // ============ 消息通信 ============
    function sendProgress(percent, text) {
        chrome.runtime.sendMessage({
            type: 'progress',
            percent: percent,
            text: text
        });
    }

    function sendSubtitlesReady(subtitleData) {
        chrome.runtime.sendMessage({
            type: 'subtitlesReady',
            subtitles: subtitleData
        });
    }

    function sendError(message) {
        chrome.runtime.sendMessage({
            type: 'error',
            message: message
        });
    }

    // ============ 主处理逻辑 ============
    async function handleGenerateSubtitles(genSettings) {
        if (isProcessing) {
            throw new Error('正在生成中，请稍候');
        }

        isProcessing = true;

        try {
            const service = genSettings.whisperService || 'local';

            if (service === 'local') {
                // 检查本地服务
                const isAvailable = await checkWhisperService();
                if (!isAvailable) {
                    throw new Error('本地 Whisper 服务未运行。请先启动 whisper-server/start.bat');
                }

                // 获取当前视频 URL
                const videoUrl = window.location.href;

                sendProgress(5, '正在连接 Whisper 服务...');

                // 开始转录任务
                const task = await startWhisperTranscription(videoUrl, genSettings.language, genSettings.api_key);
                currentTaskId = task.task_id;

                sendProgress(10, '任务已提交，开始处理...');

                // 等待完成
                const result = await pollTaskStatus(task.task_id);

                if (result.subtitles && result.subtitles.length > 0) {
                    subtitles = result.subtitles;
                    sendSubtitlesReady(subtitles);
                    createSubtitleContainer();
                    sendProgress(100, `字幕生成完成！共 ${subtitles.length} 条`);
                } else {
                    throw new Error('未识别到任何内容');
                }

            } else if (service === 'openai') {
                throw new Error('OpenAI API 暂未实现，请使用本地 Whisper 服务');
            } else {
                // 浏览器内置方式
                await generateWithBrowserAPI(genSettings.language);
            }

        } finally {
            isProcessing = false;
            currentTaskId = null;
        }
    }

    // 浏览器内置识别（备用方案）
    async function generateWithBrowserAPI(language) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

        if (!SpeechRecognition) {
            throw new Error('浏览器不支持语音识别，请使用本地 Whisper 服务');
        }

        sendProgress(5, '准备录制音频...');

        if (!videoElement) findVideoElement();
        const duration = videoElement.duration;

        if (!duration || duration === Infinity) {
            throw new Error('无法获取视频时长');
        }

        sendProgress(10, '开始识别音频（需要播放视频）...');

        const recognition = new SpeechRecognition();
        recognition.continuous = true;
        recognition.interimResults = false;
        recognition.lang = getLanguageCode(language);

        const generatedSubtitles = [];
        let startTime = 0;

        return new Promise((resolve, reject) => {
            recognition.onresult = (event) => {
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    if (event.results[i].isFinal) {
                        const text = event.results[i][0].transcript.trim();
                        if (text) {
                            const currentTime = videoElement.currentTime;
                            generatedSubtitles.push({
                                start: startTime,
                                end: currentTime,
                                text: text
                            });
                            startTime = currentTime;

                            const percent = Math.min(90, 10 + (currentTime / duration) * 80);
                            sendProgress(percent, `已识别 ${generatedSubtitles.length} 条字幕...`);
                        }
                    }
                }
            };

            recognition.onerror = (event) => {
                if (event.error !== 'no-speech') {
                    console.error('识别错误:', event.error);
                }
            };

            recognition.onend = () => {
                if (videoElement.currentTime < duration - 1) {
                    try {
                        recognition.start();
                    } catch (e) { }
                } else {
                    subtitles = generatedSubtitles;
                    sendProgress(100, '字幕生成完成！');
                    sendSubtitlesReady(generatedSubtitles);
                    createSubtitleContainer();
                    resolve(generatedSubtitles);
                }
            };

            videoElement.currentTime = 0;
            videoElement.play().then(() => {
                recognition.start();
            }).catch(reject);

            const onEnded = () => {
                recognition.stop();
                videoElement.removeEventListener('ended', onEnded);
                subtitles = generatedSubtitles;
                sendSubtitlesReady(generatedSubtitles);
                createSubtitleContainer();
                resolve(generatedSubtitles);
            };
            videoElement.addEventListener('ended', onEnded);
        });
    }

    function getLanguageCode(lang) {
        const codes = {
            'zh': 'zh-CN',
            'en': 'en-US',
            'ja': 'ja-JP',
            'ko': 'ko-KR',
            'es': 'es-ES',
            'fr': 'fr-FR',
            'de': 'de-DE',
            'ru': 'ru-RU',
            'auto': 'zh-CN'
        };
        return codes[lang] || 'zh-CN';
    }

    // ============ 消息监听 ============
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
        console.log('收到消息:', message.action);

        switch (message.action) {
            case 'generateSubtitles':
                handleGenerateSubtitles(message.settings)
                    .then(() => sendResponse({ success: true }))
                    .catch(e => {
                        sendError(e.message);
                        sendResponse({ success: false, error: e.message });
                    });
                return true;

            case 'loadSubtitles':
                subtitles = message.subtitles || [];
                settings = { ...settings, ...message.settings };
                if (subtitles.length) {
                    createSubtitleContainer();
                    onTimeUpdate();
                }
                sendResponse({ success: true });
                break;

            case 'toggleSubtitles':
                isVisible = message.visible;
                if (subtitleContainer) {
                    subtitleContainer.style.opacity = isVisible ? 1 : 0;
                }
                sendResponse({ success: true });
                break;

            case 'updateStyle':
                settings = { ...settings, ...message.settings };
                updateSubtitleStyle();
                sendResponse({ success: true });
                break;

            case 'checkService':
                checkWhisperService()
                    .then(available => sendResponse({ available }))
                    .catch(() => sendResponse({ available: false }));
                return true;

            default:
                sendResponse({ success: false, error: 'Unknown action' });
        }

        return true;
    });

    // ============ 页面导航处理 ============
    let lastUrl = location.href;
    new MutationObserver(() => {
        if (location.href !== lastUrl) {
            lastUrl = location.href;
            subtitles = [];
            removeSubtitleContainer();
            findVideoElement();
            setupVideoListeners();
        }
    }).observe(document, { subtree: true, childList: true });

    // ============ 启动 ============
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
