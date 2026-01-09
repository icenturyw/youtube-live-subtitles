// Background Service Worker
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
        // 可以在这里执行一些初始化操作
        console.log('YouTube 视频页面已加载:', tab.url);
    }
});

// 监听来自 content script 的消息
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'status') {
        // 转发状态到 popup（如果打开的话）
        console.log('状态更新:', message);
    }
    return true;
});
