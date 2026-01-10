// Popup 控制脚本 - 支持本地 Whisper 服务
document.addEventListener('DOMContentLoaded', async () => {
    // DOM 元素
    const generateBtn = document.getElementById('generateBtn');
    const batchBtn = document.getElementById('batchBtn');
    const statusIndicator = document.getElementById('statusIndicator');
    const statusText = document.getElementById('statusText');
    const progressContainer = document.getElementById('progressContainer');
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');
    const subtitleControls = document.getElementById('subtitleControls');
    const subtitleToggle = document.getElementById('subtitleToggle');
    const toggleText = document.getElementById('toggleText');
    const downloadBtn = document.getElementById('downloadBtn');

    const languageSelect = document.getElementById('language');
    const whisperServiceSelect = document.getElementById('whisperService');
    const apiKeySetting = document.getElementById('apiKeySetting');
    const apiKeyInput = document.getElementById('apiKey');
    const fontSizeSlider = document.getElementById('fontSize');
    const fontSizeValue = document.getElementById('fontSizeValue');
    const positionSelect = document.getElementById('position');

    let currentVideoId = null;
    let subtitlesVisible = true;
    let currentSubtitles = null;

    // 初始化
    await loadSettings();
    await checkService();
    await checkExistingSubtitles();

    // 检查是否显示批量按钮
    const { isYouTube, playlistId } = await checkYouTubePage();
    if (isYouTube && playlistId) {
        batchBtn.style.display = 'flex';
    }

    setupProgressListener();

    // 服务选择变更
    whisperServiceSelect.addEventListener('change', async () => {
        const service = whisperServiceSelect.value;
        // 只有 OpenAI 服务需要 API Key，本地模式现在是纯本地运行了
        apiKeySetting.style.display = (service === 'openai') ? 'block' : 'none';
        saveSettings();

        if (service === 'local') {
            await checkService();
        }
    });

    // 生成字幕按钮
    generateBtn.addEventListener('click', async () => {
        const { isYouTube, tab, videoId } = await checkYouTubePage();

        if (!isYouTube) {
            showError('请在 YouTube 视频页面使用此扩展！');
            return;
        }

        if (!videoId) {
            showError('无法获取视频 ID');
            return;
        }

        // 检查本地服务
        if (whisperServiceSelect.value === 'local') {
            const available = await checkServiceAvailable(tab.id);
            if (!available) {
                showError('本地 Whisper 服务未运行！请先启动 whisper-server/start.bat');
                return;
            }
        }

        currentVideoId = videoId;
        await startSubtitleGeneration(tab);
    });

    // 批量生成按钮
    batchBtn.addEventListener('click', async () => {
        const { isYouTube, tab, playlistId } = await checkYouTubePage();

        if (!isYouTube || !playlistId) {
            showError('未检测到播放列表');
            return;
        }

        // 仅支持本地服务
        if (whisperServiceSelect.value !== 'local') {
            showError('批量生成功能仅支持本地 Whisper 服务');
            return;
        }

        batchBtn.disabled = true;
        batchBtn.querySelector('.btn-text').textContent = '提交请求中...';

        try {
            const response = await fetch('http://127.0.0.1:8765/transcribe_playlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    playlist_url: `https://www.youtube.com/playlist?list=${playlistId}`,
                    language: languageSelect.value
                })
            });

            const data = await response.json();

            if (data.error) {
                showError(data.error);
                batchBtn.disabled = false;
                batchBtn.querySelector('.btn-text').textContent = '批量生成列表字幕';
            } else {
                statusText.textContent = '批量任务已后台提交！';
                statusIndicator.className = 'status-indicator processing';
                batchBtn.querySelector('.btn-text').textContent = '已提交后台';
                setTimeout(() => {
                    batchBtn.style.display = 'none'; // 提交后隐藏按钮或恢复
                }, 2000);
            }
        } catch (e) {
            showError('连接本地服务失败');
            batchBtn.disabled = false;
            batchBtn.querySelector('.btn-text').textContent = '批量生成列表字幕';
        }
    });

    // 字幕开关变更
    subtitleToggle.addEventListener('change', async () => {
        subtitlesVisible = subtitleToggle.checked;
        toggleText.textContent = subtitlesVisible ? '字幕已开启' : '字幕已关闭';

        saveSettings();

        const { tab } = await checkYouTubePage();
        if (tab) {
            sendMessageToContentScript(tab.id, {
                action: 'toggleSubtitles',
                visible: subtitlesVisible
            });
        }
    });

    // 下载字幕按钮
    downloadBtn.addEventListener('click', async () => {
        if (!currentSubtitles || !currentVideoId) {
            showError('没有可下载的字幕');
            return;
        }

        const srtContent = convertToSRT(currentSubtitles);
        downloadFile(srtContent, `${currentVideoId}.srt`, 'text/plain');
    });

    // 设置变更监听
    languageSelect.addEventListener('change', saveSettings);
    apiKeyInput.addEventListener('change', saveSettings);
    fontSizeSlider.addEventListener('input', () => {
        fontSizeValue.textContent = fontSizeSlider.value + 'px';
        saveSettings();
        updateSubtitleStyle();
    });
    positionSelect.addEventListener('change', () => {
        saveSettings();
        updateSubtitleStyle();
    });

    // ============ 函数定义 ============ 

    async function loadSettings() {
        const settings = await chrome.storage.local.get([
            'language', 'whisperService', 'apiKey', 'fontSize', 'position', 'subtitlesVisible'
        ]);

        if (settings.language) languageSelect.value = settings.language;
        if (settings.whisperService) {
            whisperServiceSelect.value = settings.whisperService;
            apiKeySetting.style.display = (settings.whisperService === 'openai') ? 'block' : 'none';
        }
        if (settings.apiKey) apiKeyInput.value = settings.apiKey;
        if (settings.fontSize) {
            fontSizeSlider.value = settings.fontSize;
            fontSizeValue.textContent = settings.fontSize + 'px';
        }
        if (settings.position) positionSelect.value = settings.position;

        if (settings.subtitlesVisible !== undefined) {
            subtitlesVisible = settings.subtitlesVisible;
            subtitleToggle.checked = subtitlesVisible;
            toggleText.textContent = subtitlesVisible ? '字幕已开启' : '字幕已关闭';
        }
    }

    async function saveSettings() {
        await chrome.storage.local.set({
            language: languageSelect.value,
            whisperService: whisperServiceSelect.value,
            apiKey: apiKeyInput.value,
            fontSize: parseInt(fontSizeSlider.value),
            position: positionSelect.value,
            subtitlesVisible: subtitleToggle.checked
        });
    }

    async function checkService() {
        if (whisperServiceSelect.value === 'local') {
            try {
                // 添加超时控制，3秒超时
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 3000);

                const response = await fetch('http://127.0.0.1:8765/', {
                    method: 'GET',
                    mode: 'cors',
                    signal: controller.signal
                });
                clearTimeout(timeoutId);

                if (response.ok) {
                    const data = await response.json();
                    statusIndicator.className = 'status-indicator';
                    // 显示队列状态
                    if (data.queue_size !== undefined && data.queue_size > 0) {
                        statusText.textContent = `服务就绪 (队列: ${data.queue_size})`;
                    } else {
                        statusText.textContent = 'Whisper 服务已就绪';
                    }
                } else {
                    throw new Error();
                }
            } catch (e) {
                statusIndicator.className = 'status-indicator error';
                if (e.name === 'AbortError') {
                    statusText.textContent = 'Whisper 服务连接超时';
                } else {
                    statusText.textContent = 'Whisper 服务未运行';
                }
            }
        }
    }

    async function checkServiceAvailable(tabId) {
        try {
            const result = await sendMessageToContentScript(tabId, {
                action: 'checkService'
            });
            return result.available;
        } catch (e) {
            return false;
        }
    }

    async function checkYouTubePage() {
        try {
            const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
            if (!tab.url || !tab.url.includes('youtube.com/watch')) {
                return { isYouTube: false, tab: null, videoId: null, playlistId: null };
            }

            const url = new URL(tab.url);
            const videoId = url.searchParams.get('v');
            const playlistId = url.searchParams.get('list');
            return { isYouTube: true, tab, videoId, playlistId };
        } catch (e) {
            return { isYouTube: false, tab: null, videoId: null, playlistId: null };
        }
    }

    async function checkExistingSubtitles() {
        const { videoId } = await checkYouTubePage();
        if (!videoId) return;

        currentVideoId = videoId;
        const cached = await chrome.storage.local.get(`subtitles_${videoId}`);

        if (cached[`subtitles_${videoId}`]) {
            currentSubtitles = cached[`subtitles_${videoId}`];
            showSubtitlesReady();

            const { tab } = await checkYouTubePage();
            if (tab) {
                await ensureContentScriptLoaded(tab.id);
                sendMessageToContentScript(tab.id, {
                    action: 'loadSubtitles',
                    subtitles: currentSubtitles,
                    settings: getCurrentSettings()
                });
            }
        }
    }

    async function startSubtitleGeneration(tab) {
        generateBtn.disabled = true;
        showProcessing('连接 Whisper 服务...');

        try {
            await ensureContentScriptLoaded(tab.id);

            const result = await sendMessageToContentScript(tab.id, {
                action: 'generateSubtitles',
                settings: {
                    language: languageSelect.value,
                    whisperService: whisperServiceSelect.value,
                    api_key: apiKeyInput.value
                }
            });

            if (!result.success && result.error) {
                showError(result.error);
                generateBtn.disabled = false;
            }
        } catch (e) {
            showError('连接失败，请刷新页面后重试');
            generateBtn.disabled = false;
        }
    }

    function setupProgressListener() {
        chrome.runtime.onMessage.addListener((message) => {
            if (message.type === 'progress') {
                updateProgress(message.percent, message.text);
            } else if (message.type === 'subtitlesReady') {
                currentSubtitles = message.subtitles;
                if (currentVideoId) {
                    chrome.storage.local.set({
                        [`subtitles_${currentVideoId}`]: message.subtitles
                    });
                }
                showSubtitlesReady();
                generateBtn.disabled = false;
            } else if (message.type === 'error') {
                showError(message.message);
                generateBtn.disabled = false;
            }
        });
    }

    async function ensureContentScriptLoaded(tabId) {
        try {
            await chrome.scripting.executeScript({
                target: { tabId },
                files: ['content.js']
            });
            await chrome.scripting.insertCSS({
                target: { tabId },
                files: ['subtitles.css']
            });
        } catch (e) { }
    }

    async function sendMessageToContentScript(tabId, message) {
        try {
            return await chrome.tabs.sendMessage(tabId, message);
        } catch (e) {
            // 尝试注入后重试
            await ensureContentScriptLoaded(tabId);
            await new Promise(r => setTimeout(r, 500));
            return await chrome.tabs.sendMessage(tabId, message);
        }
    }

    function getCurrentSettings() {
        return {
            fontSize: parseInt(fontSizeSlider.value),
            position: positionSelect.value
        };
    }

    async function updateSubtitleStyle() {
        const { tab } = await checkYouTubePage();
        if (tab) {
            sendMessageToContentScript(tab.id, {
                action: 'updateStyle',
                settings: getCurrentSettings()
            }).catch(e => console.debug('Style update failed:', e));
        }
    }

    // UI 更新
    function showProcessing(text) {
        statusIndicator.className = 'status-indicator processing';
        statusText.textContent = text;
        progressContainer.style.display = 'block';
        progressFill.style.width = '0%';
        progressText.textContent = text;
        subtitleControls.style.display = 'none';
    }

    function updateProgress(percent, text) {
        progressFill.style.width = percent + '%';
        progressText.textContent = text;
        statusText.textContent = text;
    }

    function showSubtitlesReady() {
        statusIndicator.className = 'status-indicator active';
        statusText.textContent = '字幕已就绪';
        progressContainer.style.display = 'none';
        subtitleControls.style.display = 'flex';
        const btnText = generateBtn.querySelector('.btn-text');
        if (btnText) btnText.textContent = '重新生成';
        generateBtn.disabled = false;
    }

    function showError(message) {
        statusIndicator.className = 'status-indicator error';
        statusText.textContent = message;
        progressContainer.style.display = 'none';

        setTimeout(() => {
            checkService();
        }, 5000);
    }

    // 工具函数
    function convertToSRT(subtitles) {
        return subtitles.map((sub, index) => {
            const startTime = formatSRTTime(sub.start);
            const endTime = formatSRTTime(sub.end);
            return `${index + 1}\n${startTime} --> ${endTime}\n${sub.text}\n`;
        }).join('\n');
    }

    function formatSRTTime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        const ms = Math.floor((seconds % 1) * 1000);
        return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')},${ms.toString().padStart(3, '0')}`;
    }

    function downloadFile(content, filename, mimeType) {
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    }
});
