import csv
import json

csv_path = r'c:\Users\Administrator\Desktop\default_project\youtube-live-subtitles\subtitles_rows.csv'

with open(csv_path, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader):
        print(f"Row {i}:")
        print(f"  video_id: {row.get('video_id')}")
        print(f"  language: {row.get('language')}")
        print(f"  subtitles prefix: {str(row.get('subtitles'))[:100]}...")
        try:
            subs = json.loads(row.get('subtitles'))
            print(f"  subtitles is valid JSON, length: {len(subs)}")
        except Exception as e:
            print(f"  subtitles is NOT valid JSON: {e}")
        if i >= 2:
            break
