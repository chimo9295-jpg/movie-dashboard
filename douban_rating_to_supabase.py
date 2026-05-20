import os
import requests
from datetime import datetime, date
import sys
sys.stdout.reconfigure(encoding="utf-8")
from supabase import create_client, Client

TOKEN = os.environ.get("JUSTONEAPI_TOKEN", "5bhSWeW0h2ST0pZA")
SUBJECT_ID = "37116446"
MOVIE_NAME = "给阿嬷的情书"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ebmncqnzammtplpwlveb.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_36nGYLplp0DYcGbTx6GWpA_K11Jb9Gd")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_rating():
    url = "https://api.justoneapi.com/api/douban/get-subject-detail/v1"
    params = {"token": TOKEN, "subjectId": SUBJECT_ID}
    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        return None, None
    data = resp.json()
    if data.get("code") != 0:
        return None, None
    rating = data.get("data", {}).get("rating")
    rating_count = data.get("data", {}).get("rating_count")
    return rating, rating_count

def save_rating(score, votes):
    today = date.today().isoformat()
    
    # 检查今天是否已有记录
    existing = supabase.table('movie_realtime').select('id').eq('crawl_date', today).execute()
    if existing.data:
        print(f"今日评分已存在，跳过更新")
        return
    
    # 插入新记录（保留历史）
    supabase.table('movie_realtime').insert({
        "movie_name": MOVIE_NAME,
        "douban_score": float(score),
        "douban_votes": int(votes),
        "crawl_time": datetime.now().isoformat(),
        "crawl_date": today
    }).execute()
    print(f"✅ 插入评分: {score} 分, {votes} 人评价 (日期: {today})")

def main():
    print("正在获取豆瓣评分...")
    score, votes = fetch_rating()
    if score and votes:
        print(f"评分: {score}, 人数: {votes}")
        save_rating(score, votes)
    else:
        print("❌ 获取评分失败")

if __name__ == "__main__":
    main()