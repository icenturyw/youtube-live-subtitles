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

// 监听来自 content script 和 popup 的消息
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    // 代理请求到本地 Whisper 服务
    if (message.type === 'proxyFetch') {
        handleProxyFetch(message)
            .then(result => sendResponse(result))
            .catch(err => sendResponse({ error: err.message }));
        return true; // 保持消息通道开放
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
