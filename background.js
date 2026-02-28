// Background Service Worker - 支持代理本地服务请求 (动态检测服务器地址)

chrome.runtime.onInstalled.addListener(() => {
    console.log('YouTube 实时字幕扩展已安装');

    // 初始化存储
    chrome.storage.local.set({
        language: 'zh-CN',
        fontSize: 24,
        position: 'bottom',
        showInterim: true,
        isRunning: false
    });
});

// 监听 tab 更新
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status === 'complete' && tab.url && tab.url.includes('youtube.com/watch')) {
        console.log('YouTube 视频页面已加载:', tab.url);
    }
});

// 监听长连接以支持 SSE 流式代理
chrome.runtime.onConnect.addListener(port => {
    if (port.name === 'proxyStream') {
        let abortController = null;

        port.onMessage.addListener(async (msg) => {
            if (msg.action === 'start') {
                const { url, options } = msg;
                const settings = await chrome.storage.local.get(['serverHost']);
                const WHISPER_SERVER = settings.serverHost || 'http://127.0.0.1:8765';
                const fullUrl = url.startsWith('http') ? url : `${WHISPER_SERVER.replace(/\/$/, '')}${url.startsWith('/') ? '' : '/'}${url}`;

                abortController = new AbortController();
                const fetchOptions = {
                    ...options,
                    signal: abortController.signal
                };

                try {
                    const response = await fetch(fullUrl, fetchOptions);

                    if (!response.ok) {
                        port.postMessage({ type: 'error', error: `HTTP ${response.status}: ${response.statusText}` });
                        port.disconnect();
                        return;
                    }

                    port.postMessage({ type: 'connected' });

                    const reader = response.body.getReader();
                    const decoder = new TextDecoder('utf-8');

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) {
                            port.postMessage({ type: 'end' });
                            break;
                        }

                        const chunk = decoder.decode(value, { stream: true });
                        port.postMessage({ type: 'chunk', chunk });
                    }
                } catch (error) {
                    if (error.name !== 'AbortError') {
                        port.postMessage({ type: 'error', error: error.message });
                    }
                } finally {
                    port.disconnect();
                }
            } else if (msg.action === 'abort') {
                if (abortController) {
                    abortController.abort();
                }
            }
        });

        port.onDisconnect.addListener(() => {
            if (abortController) {
                abortController.abort();
            }
        });
    }
});

// 监听来自 content script 和 popup 的消息
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    // 代理请求到本地 Whisper 服务
    if (message.type === 'proxyFetch') {
        handleProxyFetch(message)
            .then(result => sendResponse(result))
            .catch(err => sendResponse({ error: err.message }));
        return true; // 保持消息通道开放
    }

    // 视频下载：使用 chrome.downloads API
    if (message.type === 'downloadVideo') {
        const { downloadUrl, filename } = message;
        chrome.downloads.download({
            url: downloadUrl,
            filename: filename,
            saveAs: true
        }, (downloadId) => {
            if (chrome.runtime.lastError) {
                sendResponse({ error: chrome.runtime.lastError.message });
            } else {
                sendResponse({ success: true, downloadId });
            }
        });
        return true;
    }

    if (message.type === 'status') {
        console.log('状态更新:', message);
    }

    return true;
});

// 代理 fetch 请求
async function handleProxyFetch(message) {
    const { url, options } = message;

    // 获取存储的服务器地址
    const settings = await chrome.storage.local.get(['serverHost']);
    const WHISPER_SERVER = settings.serverHost || 'http://127.0.0.1:8765';

    const fullUrl = url.startsWith('http') ? url : `${WHISPER_SERVER.replace(/\/$/, '')}${url.startsWith('/') ? '' : '/'}${url}`;

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000); // 30秒超时

        const response = await fetch(fullUrl, {
            ...options,
            signal: controller.signal
        });
        clearTimeout(timeoutId);

        const data = await response.json();
        return {
            ok: response.ok,
            status: response.status,
            data
        };
    } catch (error) {
        if (error.name === 'AbortError') {
            return { error: '请求超时' };
        }
        return { error: error.message };
    }
}
