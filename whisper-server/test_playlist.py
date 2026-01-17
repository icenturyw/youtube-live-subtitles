import subprocess
import json
import sys

# 测试用的播放列表 (YouTube 官方的一个短列表)
TEST_PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL15B1E77BB5708555"

def get_playlist_info(url):
    print(f"正在解析列表: {url} ...")
    cmd = [
        'yt-dlp',
        '--flat-playlist',  # 不下载视频，只列出信息
        '--dump-single-json', # 输出为 JSON
        '--no-playlist', # 这里的 no-playlist 是指不下载视频内容，但 flat-playlist 会生效
        url
    ]
    
    # 注意：对于 playlist，不需要 --no-playlist 参数，反而需要去掉它或者明确指定处理 list
    # 正确的参数组合应该是下面这样：
    cmd = [
        'yt-dlp',
        '--flat-playlist',
        '--dump-single-json',
        url
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode != 0:
            print(f"错误: {result.stderr}")
            return None
        
        data = json.loads(result.stdout)
        return data
    except Exception as e:
        print(f"异常: {e}")
        return None

if __name__ == "__main__":
    data = get_playlist_info(TEST_PLAYLIST_URL)
    if data:
        print(f"列表标题: {data.get('title')}")
        entries = data.get('entries', [])
        print(f"找到 {len(entries)} 个视频:")
        for i, entry in enumerate(entries[:5]): # 只打印前5个
            print(f"  {i+1}. [{entry.get('id')}] {entry.get('title')}")
    else:
        print("解析失败")
