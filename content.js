// YouTube 字幕生成器 - Content Script（支持本地 Whisper 服务）
(function () {
    'use strict';

    // ============ 配置 ============
    let WHISPER_SERVER = 'http://127.0.0.1:8765';
    let SERVER_AUTH_KEY = '';

    // 初始化配置
    chrome.storage.local.get(['serverHost', 'authKey'], (result) => {
        if (result.serverHost) WHISPER_SERVER = result.serverHost.replace(/\/$/, '');
        if (result.authKey) SERVER_AUTH_KEY = result.authKey;
    });

    // ============ 代理 fetch 函数 (绕过 PNA 限制) ============
    async function proxyFetch(url, options = {}) {
        // 自动注入鉴权 Key
        if (url.startsWith('//') || !url.includes('://')) {
            // 补全相对路径
            url = `${WHISPER_SERVER}${url.startsWith('/') ? '' : '/'}${url}`;
        }

        if (url.startsWith(WHISPER_SERVER)) {
            options.headers = options.headers || {};
            if (SERVER_AUTH_KEY) {
                options.headers['X-API-Key'] = SERVER_AUTH_KEY;
            }
        }
        try {
            const result = await chrome.runtime.sendMessage({
                type: 'proxyFetch',
                url: url,
                options: {
                    method: options.method || 'GET',
                    headers: options.headers,
                    body: options.body
                }
            });
            if (result.error) {
                throw new Error(result.error);
            }
            return result;
        } catch (e) {
            console.error('Proxy fetch 失败:', e);
            if (e.message.includes('Extension context invalidated')) {
                console.warn('检测到扩展重载，正在自动刷新页面...');
                window.location.reload();
            }
            throw e;
        }
    }

    // ============ 状态变量 ============
    let subtitles = [];
    let subtitleContainer = null;
    let sidebarContainer = null;
    let sidebarVisible = false;
    let isVisible = true;
    let settings = {
        fontSize: 24,
        position: 'bottom',
        color: '#ffffff',
        backgroundColor: '#000000',
        backgroundOpacity: 0.8,
        fontFamily: 'Arial, sans-serif',
        strokeWidth: '2px',
        strokeColor: '#000000'
    };
    let videoElement = null;
    let isProcessing = false;
    let currentTaskId = null;
    let activeTranscriptIndex = -1;

    // ============ 初始化 ============
    function init() {
        findVideoElement();
        setupVideoListeners();
        // 尝试自动加载字幕
        autoLoadSubtitles();
        console.log('YouTube 字幕生成器已加载');
    }

    async function autoLoadSubtitles() {
        const videoId = new URLSearchParams(window.location.search).get('v');
        if (!videoId) return;

        try {
            const styleKeys = [
                'fontSize', 'position', 'subtitlesVisible',
                'subtitleColor', 'bgColor', 'bgOpacity',
                'fontFamily', 'strokeWidth', 'strokeColor'
            ];
            const keys = [`subtitles_${videoId}`, ...styleKeys];
            const data = await chrome.storage.local.get(keys);

            // 更新设置
            if (data.fontSize) settings.fontSize = data.fontSize;
            if (data.position) settings.position = data.position;
            if (data.subtitleColor) settings.color = data.subtitleColor;
            if (data.bgColor) settings.backgroundColor = data.bgColor;
            if (data.bgOpacity !== undefined) settings.backgroundOpacity = data.bgOpacity / 100;
            if (data.fontFamily) settings.fontFamily = data.fontFamily;
            if (data.strokeWidth) settings.strokeWidth = data.strokeWidth;
            if (data.strokeColor) settings.strokeColor = data.strokeColor;

            if (data.subtitlesVisible !== undefined) isVisible = data.subtitlesVisible;

            // 加载字幕
            if (data[`subtitles_${videoId}`]) {
                subtitles = data[`subtitles_${videoId}`];
                console.log('已自动加载本地字幕:', subtitles.length, '条');
                createSubtitleContainer();
                onTimeUpdate();
            } else {
                // 尝试从本地服务器加载
                await tryLoadFromLocalServer(videoId);
            }
        } catch (e) {
            console.error('自动加载字幕失败:', e);
        }
    }

    async function tryLoadFromLocalServer(videoId) {
        try {
            const result = await proxyFetch(`${WHISPER_SERVER}/status/${videoId}`);
            if (!result.ok) return;

            const data = result.data;
            if (data.status === 'completed' && data.subtitles) {
                console.log('从本地服务同步字幕');
                subtitles = data.subtitles;

                // 保存到浏览器缓存
                chrome.storage.local.set({
                    [`subtitles_${videoId}`]: subtitles
                });

                createSubtitleContainer();
                onTimeUpdate();
            }
        } catch (e) {
            // 服务可能未运行，忽略
        }
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
        if (!subtitles.length || !videoElement) return;

        const currentTime = videoElement.currentTime;
        const subtitleIndex = findSubtitleIndexAtTime(currentTime);

        if (subtitleIndex !== -1) {
            const subtitle = subtitles[subtitleIndex];
            if (isVisible) showSubtitle(subtitle); // 传整个对象以便显示译文
            updateActiveTranscriptItem(subtitleIndex);
        } else {
            if (isVisible) showSubtitle(null);
        }
    }

    // 查找当前时间的字幕索引
    function findSubtitleIndexAtTime(time) {
        let left = 0;
        let right = subtitles.length - 1;

        while (left <= right) {
            const mid = Math.floor((left + right) / 2);
            const sub = subtitles[mid];

            if (time >= sub.start && time <= sub.end) {
                return mid;
            } else if (time < sub.start) {
                right = mid - 1;
            } else {
                left = mid + 1;
            }
        }
        return -1;
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

            // 如果有字幕且没有侧边栏开关，创建一个
            createSidebarToggle();
        } else {
            document.body.appendChild(subtitleContainer);
        }

        console.log('字幕容器已创建');
    }

    function createSidebarToggle() {
        let toggleBtn = document.getElementById('yt-sidebar-toggle');
        if (toggleBtn) return;

        toggleBtn = document.createElement('button');
        toggleBtn.id = 'yt-sidebar-toggle';
        toggleBtn.className = 'yt-sidebar-toggle-btn';
        toggleBtn.innerHTML = '📝';
        toggleBtn.title = '打开/关闭转录稿侧边栏';

        toggleBtn.onclick = () => {
            toggleSidebar();
        };

        document.body.appendChild(toggleBtn);
    }

    function toggleSidebar() {
        if (!sidebarContainer) {
            createSidebar();
        }

        sidebarVisible = !sidebarVisible;
        if (sidebarVisible) {
            sidebarContainer.classList.remove('collapsed');
            renderTranscript();
        } else {
            sidebarContainer.classList.add('collapsed');
        }
    }

    function createSidebar() {
        if (sidebarContainer) return;

        sidebarContainer = document.createElement('div');
        sidebarContainer.className = 'yt-transcript-sidebar collapsed';

        sidebarContainer.innerHTML = `
            <div class="yt-transcript-header">
                <span class="yt-transcript-title">转录详情</span>
                <button class="yt-transcript-close">×</button>
            </div>
            <div class="yt-transcript-content" id="yt-transcript-content">
                <!-- 列表项将在这里渲染 -->
            </div>
        `;

        document.body.appendChild(sidebarContainer);

        sidebarContainer.querySelector('.yt-transcript-close').onclick = () => {
            toggleSidebar();
        };
    }

    function renderTranscript() {
        const content = document.getElementById('yt-transcript-content');
        if (!content) return;

        content.innerHTML = '';
        subtitles.forEach((sub, index) => {
            const item = document.createElement('div');
            item.className = 'yt-transcript-item';
            if (index === activeTranscriptIndex) item.classList.add('active');
            item.dataset.index = index;

            const timeStr = formatTime(sub.start);
            const textHtml = sub.translation
                ? `<div class="original">${sub.text}</div><div class="translation">${sub.translation}</div>`
                : `<div class="original">${sub.text}</div>`;

            item.innerHTML = `
                <div class="yt-transcript-time">${timeStr}</div>
                <div class="yt-transcript-text">${textHtml}</div>
            `;

            item.onclick = () => {
                if (videoElement) {
                    videoElement.currentTime = sub.start;
                    videoElement.play();
                }
            };

            content.appendChild(item);
        });

        // 滚动到当前项
        scrollToActiveItem();
    }

    function updateActiveTranscriptItem(index) {
        if (activeTranscriptIndex === index) return;

        activeTranscriptIndex = index;

        if (!sidebarVisible) return;

        const content = document.getElementById('yt-transcript-content');
        if (!content) return;

        // 移除旧的高亮
        const prevActive = content.querySelector('.yt-transcript-item.active');
        if (prevActive) prevActive.classList.remove('active');

        // 添加新的高亮
        const newActive = content.querySelector(`.yt-transcript-item[data-index="${index}"]`);
        if (newActive) {
            newActive.classList.add('active');
            scrollToActiveItem();
        }
    }

    function scrollToActiveItem() {
        const content = document.getElementById('yt-transcript-content');
        if (!content) return;

        const activeItem = content.querySelector('.yt-transcript-item.active');
        if (activeItem) {
            const containerHeight = content.clientHeight;
            const itemTop = activeItem.offsetTop;
            const itemHeight = activeItem.clientHeight;

            content.scrollTo({
                top: itemTop - (containerHeight / 2) + (itemHeight / 2),
                behavior: 'smooth'
            });
        }
    }

    function formatTime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);

        if (h > 0) {
            return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }
        return `${m}:${s.toString().padStart(2, '0')}`;
    }

    function removeSubtitleContainer() {
        const existing = document.getElementById('yt-custom-subtitle-container');
        if (existing) existing.remove();
        subtitleContainer = null;
    }

    // 辅助函数：将hex颜色转换为rgb
    function hexToRgb(hex) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        return result ? {
            r: parseInt(result[1], 16),
            g: parseInt(result[2], 16),
            b: parseInt(result[3], 16)
        } : null;
    }

    function updateSubtitleStyle() {
        if (!subtitleContainer) return;

        const positionStyles = settings.position === 'top'
            ? 'top: 60px !important; bottom: auto !important;'
            : 'bottom: 80px !important; top: auto !important;';

        subtitleContainer.style.cssText = `
            position: absolute !important;
            left: 50% !important;
            transform: translateX(-50%) !important;
            ${positionStyles}
            z-index: 999999 !important;
            max-width: 90% !important;
            text-align: center !important;
            pointer-events: none !important;
            transition: opacity 0.2s ease !important;
            opacity: ${isVisible ? 1 : 0} !important;
            display: block !important;
        `;

        const textElement = subtitleContainer.querySelector('#yt-custom-subtitle-text');
        if (textElement) {
            // 参数解析，确保单位正确
            const fSize = String(settings.fontSize).endsWith('px') ? settings.fontSize : settings.fontSize + 'px';
            const sWidth = String(settings.strokeWidth).endsWith('px') ? settings.strokeWidth : settings.strokeWidth + 'px';

            // 背景色处理
            const bgRgb = hexToRgb(settings.backgroundColor);
            const opacity = settings.backgroundOpacity !== undefined ? settings.backgroundOpacity : 0.8;
            const bgColor = bgRgb
                ? `rgba(${bgRgb.r}, ${bgRgb.g}, ${bgRgb.b}, ${opacity})`
                : `rgba(0, 0, 0, ${opacity})`;

            const css = `
                display: inline-block !important;
                padding: 10px 20px !important;
                background: ${bgColor} !important;
                color: ${settings.color} !important;
                font-size: ${fSize} !important;
                font-family: ${settings.fontFamily} !important;
                border-radius: 8px !important;
                text-shadow: ${sWidth} ${sWidth} ${sWidth} ${settings.strokeColor},
                             -${sWidth} ${sWidth} ${sWidth} ${settings.strokeColor},
                             ${sWidth} -${sWidth} ${sWidth} ${settings.strokeColor},
                             -${sWidth} -${sWidth} ${sWidth} ${settings.strokeColor} !important;
                line-height: 1.5 !important;
                max-width: 100% !important;
                word-wrap: break-word !important;
                backdrop-filter: blur(4px) !important;
                -webkit-backdrop-filter: blur(4px) !important;
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4) !important;
                visibility: visible !important;
            `;
            console.log('应用字幕样式:', css);
            textElement.style.cssText = css;
        }
    }

    function showSubtitle(sub) {
        if (!subtitleContainer) {
            if (sub) createSubtitleContainer();
            else return;
        }

        const textElement = subtitleContainer.querySelector('#yt-custom-subtitle-text');
        if (textElement) {
            if (!sub) {
                textElement.style.setProperty('display', 'none', 'important');
                return;
            }

            if (sub.translation) {
                textElement.innerHTML = `<div class="yt-sub-original">${sub.text}</div><div class="yt-sub-translation">${sub.translation}</div>`;
            } else {
                textElement.textContent = sub.text;
            }

            textElement.style.setProperty('display', 'inline-block', 'important');
        }
    }

    // ============ Whisper 服务交互 ============
    async function checkWhisperService() {
        try {
            const result = await proxyFetch(`${WHISPER_SERVER}/health`);
            return result.data && result.data.status === 'ok';
        } catch (e) {
            console.error('健康检查失败:', e);
            return false;
        }
    }

    async function startWhisperTranscription(videoUrl, language, apiKey, service, targetLang) {
        const result = await proxyFetch(`${WHISPER_SERVER}/transcribe`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                video_url: videoUrl,
                language: language === 'auto' ? 'auto' : language,
                api_key: apiKey,
                service: service || 'local',
                target_lang: targetLang
            })
        });

        if (!result.ok) {
            throw new Error(result.data?.detail || '请求失败');
        }

        return result.data;
    }

    async function pollTaskStatus(taskId) {
        // 直接使用轮询方式（SSE 无法通过消息代理）
        return await fallbackPolling(taskId);
    }

    async function fallbackPolling(taskId) {
        const maxAttempts = 3600;  // 最多等待 30 分钟 (1800 秒)

        for (let i = 0; i < maxAttempts; i++) {
            await sleep(500);

            try {
                const result = await proxyFetch(`${WHISPER_SERVER}/task/${taskId}`);
                const data = result.data;

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

            if (service === 'local' || service === 'groq' || service === 'openai') {
                // 立即更新当前配置 (优先使用弹窗输入的配置，即使未点击保存)
                if (genSettings.server_host) {
                    WHISPER_SERVER = genSettings.server_host.replace(/\/$/, '');
                }
                if (genSettings.auth_key) {
                    SERVER_AUTH_KEY = genSettings.auth_key;
                }

                // 检查服务器可用性
                const isAvailable = await checkWhisperService();
                if (!isAvailable) {
                    const isLocal = WHISPER_SERVER.includes('127.0.0.1') || WHISPER_SERVER.includes('localhost');
                    const errorMsg = isLocal
                        ? '本地 Whisper 服务未运行。请先启动 whisper-server/start.bat'
                        : `无法连接到远程服务器 (${WHISPER_SERVER})，请检查地址或鉴权 Key 是否正确。`;
                    throw new Error(errorMsg);
                }

                const videoUrl = window.location.href;

                sendProgress(5, '正在连接 Whisper 服务...');

                // 开始转录任务
                const task = await startWhisperTranscription(
                    videoUrl,
                    genSettings.language,
                    genSettings.api_key,
                    service,
                    genSettings.target_lang
                );
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
                    subtitleContainer.style.setProperty('opacity', isVisible ? '1' : '0', 'important');
                }
                sendResponse({ success: true });
                break;

            case 'updateStyle':
                console.log('收到样式更新消息:', message.style);
                // 合并新样式设置
                if (message.style) {
                    settings = { ...settings, ...message.style };
                    console.log('合并后的设置:', settings);
                    updateSubtitleStyle();
                }
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
            const oldId = new URLSearchParams(new URL(lastUrl).search).get('v');
            const newId = new URLSearchParams(window.location.search).get('v');

            lastUrl = location.href;

            if (oldId !== newId) {
                console.log('检测到视频切换，重置并尝试加载字幕');
                subtitles = [];
                removeSubtitleContainer();
                findVideoElement();
                setupVideoListeners();
                autoLoadSubtitles();
            }
        }
    }).observe(document, { subtree: true, childList: true });

    // ============ 启动 ============
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
