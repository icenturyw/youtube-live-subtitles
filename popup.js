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
    const saveSettingsBtn = document.getElementById('saveSettingsBtn');
    const progressPercentage = document.getElementById('progressPercentage');

    const languageSelect = document.getElementById('language');
    const whisperServiceSelect = document.getElementById('whisperService');
    const apiKeySetting = document.getElementById('apiKeySetting');
    const apiKeyInput = document.getElementById('apiKey');
    const fontSizeSlider = document.getElementById('fontSize');
    const fontSizeValue = document.getElementById('fontSizeValue');
    const positionSelect = document.getElementById('position');

    // 拖拽区域元素
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileInfo = document.getElementById('fileInfo');
    const fileName = document.getElementById('fileName');
    const fileSize = document.getElementById('fileSize');

    // 样式自定义元素
    const subtitleColor = document.getElementById('subtitleColor');
    const bgColor = document.getElementById('bgColor');
    const bgOpacity = document.getElementById('bgOpacity');
    const bgOpacityValue = document.getElementById('bgOpacityValue');
    const fontFamily = document.getElementById('fontFamily');
    const strokeWidth = document.getElementById('strokeWidth');
    const strokeWidthValue = document.getElementById('strokeWidthValue');
    const strokeColor = document.getElementById('strokeColor');

    // 翻译设置元素
    const translateBilingual = document.getElementById('translateBilingual');
    const targetLanguage = document.getElementById('targetLanguage');

    let currentVideoId = null;
    let subtitlesVisible = true;
    let currentSubtitles = null;
    let selectedFile = null;

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
        // OpenAI 和 Groq 服务需要 API Key
        const needsApiKey = (service === 'openai' || service === 'groq');
        apiKeySetting.style.display = needsApiKey ? 'block' : 'none';

        const apiKeyLabel = document.getElementById('apiKeyLabel');
        if (service === 'groq') {
            apiKeyLabel.textContent = 'Groq API Key';
            apiKeyInput.placeholder = 'gsk_...';
        } else if (service === 'openai') {
            apiKeyLabel.textContent = 'OpenAI API Key';
            apiKeyInput.placeholder = 'sk_...';
        }

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

        if (whisperServiceSelect.value === 'browser') {
            showError('批量生成功能暂不支持浏览器内置服务');
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
                    language: languageSelect.value,
                    service: whisperServiceSelect.value,
                    api_key: apiKeyInput.value,
                    target_lang: translateBilingual.checked ? targetLanguage.value : null
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

    // 文件拖拽区域点击
    dropZone.addEventListener('click', () => {
        fileInput.click();
    });

    // 文件选择处理
    fileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            handleFileSelect(file);
        }
    });

    // 拖拽事件
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => {
            dropZone.classList.add('drag-over');
        });
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => {
            dropZone.classList.remove('drag-over');
        });
    });

    dropZone.addEventListener('drop', (e) => {
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileSelect(files[0]);
        }
    });

    // 处理文件选择
    function handleFileSelect(file) {
        // 验证文件类型
        const validTypes = ['audio/', 'video/'];
        const isValid = validTypes.some(type => file.type.startsWith(type));

        if (!isValid) {
            showError('请选择音频或视频文件');
            return;
        }

        selectedFile = file;
        fileName.textContent = file.name;
        fileSize.textContent = formatFileSize(file.size);
        fileInfo.style.display = 'flex';

        // 自动开始上传
        uploadAndTranscribeFile();
    }

    // 格式化文件大小
    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        else if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        else if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
        else return (bytes / 1073741824).toFixed(1) + ' GB';
    }

    // 上传并转录文件
    async function uploadAndTranscribeFile() {
        if (!selectedFile) return;

        if (whisperServiceSelect.value === 'browser') {
            showError('本地文件识别暂不支持浏览器内置服务');
            return;
        }

        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('language', languageSelect.value);
        formData.append('service', whisperServiceSelect.value);
        if (apiKeyInput.value) {
            formData.append('api_key', apiKeyInput.value);
        }
        if (translateBilingual.checked) {
            formData.append('target_lang', targetLanguage.value);
        }

        showProcessing('正在上传文件...', 0);

        try {
            const response = await fetch('http://127.0.0.1:8765/upload', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (data.error) {
                showError(data.error);
                return;
            }

            const taskId = data.task_id;
            pollTaskStatus(taskId);

        } catch (e) {
            showError('连接本地服务失败: ' + e.message);
        }
    }

    // 字幕开关变更
    subtitleToggle.addEventListener('change', async () => {
        subtitlesVisible = subtitleToggle.checked;
        toggleText.textContent = subtitlesVisible ? '字幕已开启' : '字幕已关闭';

        saveSettings();

        const { tab } = await checkYouTubePage();
        if (tab) {
            try {
                await chrome.tabs.sendMessage(tab.id, {
                    action: 'toggleSubtitles',
                    visible: subtitlesVisible
                });
            } catch (e) {
                console.log('无法发送字幕切换消息:', e);
            }
        }
    });

    // 下载按钮
    downloadBtn.addEventListener('click', async () => {
        if (!currentSubtitles || !currentVideoId) {
            showError('没有可下载的字幕');
            return;
        }

        const srtContent = convertToSRT(currentSubtitles);
        downloadFile(srtContent, `${currentVideoId}.srt`, 'text/plain');
    });

    // 样式变更监听
    fontSizeSlider.addEventListener('input', () => {
        fontSizeValue.textContent = fontSizeSlider.value + 'px';
        updateSubtitleStyle();
    });

    bgOpacity.addEventListener('input', () => {
        bgOpacityValue.textContent = bgOpacity.value + '%';
        updateSubtitleStyle();
    });

    strokeWidth.addEventListener('input', () => {
        strokeWidthValue.textContent = strokeWidth.value + 'px';
        updateSubtitleStyle();
    });

    subtitleColor.addEventListener('input', () => updateSubtitleStyle());
    bgColor.addEventListener('input', () => updateSubtitleStyle());
    fontFamily.addEventListener('change', () => updateSubtitleStyle());
    strokeColor.addEventListener('input', () => updateSubtitleStyle());
    positionSelect.addEventListener('change', () => updateSubtitleStyle());

    // 保存配置按钮
    saveSettingsBtn.addEventListener('click', async () => {
        saveSettingsBtn.disabled = true;
        const originalText = saveSettingsBtn.innerHTML;
        saveSettingsBtn.innerHTML = '<span>⏳</span> 正在保存...';

        await saveSettings();
        await updateSubtitleStyle();

        setTimeout(() => {
            saveSettingsBtn.classList.add('saved');
            saveSettingsBtn.innerHTML = '<span>✅</span> 配置已保存';

            setTimeout(() => {
                saveSettingsBtn.classList.remove('saved');
                saveSettingsBtn.innerHTML = originalText;
                saveSettingsBtn.disabled = false;
            }, 2000);
        }, 500);
    });

    // ============ 函数定义 ============

    async function loadSettings() {
        const settings = await chrome.storage.local.get([
            'language', 'whisperService', 'apiKey', 'fontSize', 'position', 'subtitlesVisible',
            'subtitleColor', 'bgColor', 'bgOpacity', 'fontFamily', 'strokeWidth', 'strokeColor',
            'translateBilingual', 'targetLanguage'
        ]);

        if (settings.language) languageSelect.value = settings.language;
        if (settings.whisperService) {
            whisperServiceSelect.value = settings.whisperService;
            const needsApiKey = (settings.whisperService === 'openai' || settings.whisperService === 'groq');
            apiKeySetting.style.display = needsApiKey ? 'block' : 'none';

            const apiKeyLabel = document.getElementById('apiKeyLabel');
            if (settings.whisperService === 'groq') {
                apiKeyLabel.textContent = 'Groq API Key';
                apiKeyInput.placeholder = 'gsk_...';
            } else if (settings.whisperService === 'openai') {
                apiKeyLabel.textContent = 'OpenAI API Key';
                apiKeyInput.placeholder = 'sk_...';
            }
        }
        if (settings.apiKey) apiKeyInput.value = settings.apiKey;
        if (settings.fontSize) {
            fontSizeSlider.value = settings.fontSize;
            fontSizeValue.textContent = settings.fontSize + 'px';
        }
        if (settings.position) positionSelect.value = settings.position;

        if (settings.subtitleColor) subtitleColor.value = settings.subtitleColor;
        if (settings.bgColor) bgColor.value = settings.bgColor;
        if (settings.bgOpacity) {
            bgOpacity.value = settings.bgOpacity;
            bgOpacityValue.textContent = settings.bgOpacity + '%';
        }
        if (settings.fontFamily) fontFamily.value = settings.fontFamily;
        if (settings.strokeWidth) {
            strokeWidth.value = settings.strokeWidth;
            strokeWidthValue.textContent = settings.strokeWidth + 'px';
        }
        if (settings.strokeColor) strokeColor.value = settings.strokeColor;

        if (settings.translateBilingual !== undefined) translateBilingual.checked = settings.translateBilingual;
        if (settings.targetLanguage) targetLanguage.value = settings.targetLanguage;

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
            subtitlesVisible: subtitleToggle.checked,
            subtitleColor: subtitleColor.value,
            bgColor: bgColor.value,
            bgOpacity: parseInt(bgOpacity.value),
            fontFamily: fontFamily.value,
            strokeWidth: parseFloat(strokeWidth.value),
            strokeColor: strokeColor.value,
            translateBilingual: translateBilingual.checked,
            targetLanguage: targetLanguage.value
        });
    }

    function getCurrentSettings() {
        return {
            fontSize: fontSizeSlider.value + 'px',
            position: positionSelect.value,
            color: subtitleColor.value,
            backgroundColor: bgColor.value,
            backgroundOpacity: bgOpacity.value / 100,
            fontFamily: fontFamily.value,
            strokeWidth: strokeWidth.value + 'px',
            strokeColor: strokeColor.value
        };
    }

    async function updateSubtitleStyle() {
        const { tab } = await checkYouTubePage();
        if (tab) {
            sendMessageToContentScript(tab.id, {
                action: 'updateStyle',
                style: getCurrentSettings()
            }).catch(e => console.log('无法发送样式更新消息:', e));
        }
    }

    async function checkService() {
        if (whisperServiceSelect.value === 'local') {
            try {
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
                statusText.textContent = e.name === 'AbortError' ? 'Whisper 服务连接超时' : 'Whisper 服务未运行';
            }
        }
    }

    async function checkServiceAvailable(tabId) {
        try {
            const result = await sendMessageToContentScript(tabId, { action: 'checkService' });
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
            return { isYouTube: true, tab, videoId: url.searchParams.get('v'), playlistId: url.searchParams.get('list') };
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
            // 如果开启了双语，先清除本地缓存，强制刷新显示
            if (translateBilingual.checked && currentVideoId) {
                await chrome.storage.local.remove(`subtitles_${currentVideoId}`);
            }
            await ensureContentScriptLoaded(tab.id);
            const result = await sendMessageToContentScript(tab.id, {
                action: 'generateSubtitles',
                settings: {
                    language: languageSelect.value,
                    whisperService: whisperServiceSelect.value,
                    api_key: apiKeyInput.value,
                    target_lang: translateBilingual.checked ? targetLanguage.value : null,
                    ...getCurrentSettings()
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
                    chrome.storage.local.set({ [`subtitles_${currentVideoId}`]: message.subtitles });
                }
                showSubtitlesReady();
                renderTranscript(); // 此时更新侧边栏
                generateBtn.disabled = false;
            } else if (message.type === 'error') {
                showError(message.message);
                generateBtn.disabled = false;
            }
        });
    }

    async function ensureContentScriptLoaded(tabId) {
        try {
            await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] });
            await chrome.scripting.insertCSS({ target: { tabId }, files: ['subtitles.css', 'sidebar.css'] });
        } catch (e) { }
    }

    async function sendMessageToContentScript(tabId, message) {
        try {
            return await chrome.tabs.sendMessage(tabId, message);
        } catch (e) {
            await ensureContentScriptLoaded(tabId);
            await new Promise(r => setTimeout(r, 500));
            return await chrome.tabs.sendMessage(tabId, message);
        }
    }

    function showProcessing(text, percent = 0) {
        statusIndicator.className = 'status-indicator processing';
        statusText.textContent = text;
        progressContainer.style.display = 'block';
        progressFill.style.width = percent + '%';
        if (progressPercentage) progressPercentage.textContent = percent + '%';
        progressText.textContent = text;
        subtitleControls.style.display = 'none';
    }

    function updateProgress(percent, text) {
        progressFill.style.width = percent + '%';
        if (progressPercentage) progressPercentage.textContent = Math.round(percent) + '%';
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
        setTimeout(() => { checkService(); }, 5000);
    }

    function convertToSRT(subtitles) {
        return subtitles.map((sub, index) => {
            const startTime = formatSRTTime(sub.start);
            const endTime = formatSRTTime(sub.end);
            const text = sub.translation ? `${sub.text}\n${sub.translation}` : sub.text;
            return `${index + 1}\n${startTime} --> ${endTime}\n${text}\n`;
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
