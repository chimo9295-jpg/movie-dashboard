import os
import requests
from datetime import datetime, date, timezone, timedelta

CST = timezone(timedelta(hours=8))
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
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code != 200:
        return None, None
    data = resp.json()
    if data.get("code") != 0:
        return None, None
    rating = data.get("data", {}).get("rating")
    rating_count = data.get("data", {}).get("rating_count")
    return rating, rating_count

def save_rating(score, votes):
    today = datetime.now(CST).date().isoformat()

    existing = supabase.table('movie_realtime').select('id').eq('crawl_date', today).execute()
    if existing.data:
        record_id = existing.data[0]['id']
        supabase.table('movie_realtime').update({
            "douban_score": float(score),
            "douban_votes": int(votes),
            "crawl_time": datetime.now(timezone.utc).isoformat(),
        }).eq('id', record_id).execute()
        print(f"✅ 更新评分: {score} 分, {votes} 人评价 (日期: {today})")
        return

    supabase.table('movie_realtime').insert({
        "movie_name": MOVIE_NAME,
        "douban_score": float(score),
        "douban_votes": int(votes),
        "crawl_time": datetime.now(timezone.utc).isoformat(),
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