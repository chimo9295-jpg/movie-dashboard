import requests
import hashlib
import time
import json
import os
import sys
from datetime import datetime
from supabase import create_client, Client

sys.stdout.reconfigure(encoding='utf-8')

# ========== 配置 ==========
TOKEN = os.environ.get("JUSTONEAPI_TOKEN", "5bhSWeW0h2ST0pZA")
SUBJECT_ID = "37116446"
MOVIE_NAME = "给阿嬷的情书"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ebmncqnzammtplpwlveb.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_36nGYLplp0DYcGbTx6GWpA_K11Jb9Gd")

# 分页配置
PAGE_SIZE = 20
MAX_REVIEWS = 40

# 费用控制配置
DAILY_CALL_LIMIT = 3          # 每日最大 API 调用次数
REQUEST_INTERVAL = 60         # 多次请求之间的最小间隔（秒）
CACHE_FILE = "api_call_cache.json"  # 本地调用次数缓存文件

# ========== 初始化 Supabase ==========
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========== 费用控制模块 ==========

def load_call_cache() -> dict:
    """加载本地调用次数缓存"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                # 检查是否是今天的记录
                if cache.get('date') == datetime.now().date().isoformat():
                    return cache
        except (json.JSONDecodeError, IOError):
            pass
    # 返回今天的空记录
    return {
        'date': datetime.now().date().isoformat(),
        'call_count': 0,
        'last_call_time': None
    }

def save_call_cache(cache: dict):
    """保存调用次数缓存到本地"""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"⚠️ 缓存文件写入失败: {e}")

def check_daily_limit() -> dict:
    """
    检查今日调用次数是否超限
    返回缓存字典，调用前必须使用此函数
    """
    cache = load_call_cache()
    today = datetime.now().date().isoformat()
    
    # 如果不是今天的记录，重置
    if cache.get('date') != today:
        cache = {
            'date': today,
            'call_count': 0,
            'last_call_time': None
        }
        save_call_cache(cache)
        print(f"📅 新的一天，调用次数已重置")
    
    print(f"📊 今日已调用 API {cache['call_count']} 次，上限 {DAILY_CALL_LIMIT} 次")
    
    if cache['call_count'] >= DAILY_CALL_LIMIT:
        print(f"🚫 今日调用次数已达上限 ({DAILY_CALL_LIMIT} 次)，停止调用以节省费用！")
        print(f"   如确需继续，请手动删除缓存文件: {CACHE_FILE}")
        return cache
    
    return cache

def record_api_call(cache: dict):
    """记录一次 API 调用"""
    cache['call_count'] += 1
    cache['last_call_time'] = datetime.now().isoformat()
    save_call_cache(cache)
    
    remaining = DAILY_CALL_LIMIT - cache['call_count']
    print(f"💰 API 调用已记录，今日剩余 {remaining} 次")
    
    if remaining <= 0:
        print(f"⚠️ 费用预警：今日 API 调用次数已用完！")
    elif remaining == 1:
        print(f"⚠️ 费用预警：今日仅剩 1 次调用机会！")

def enforce_request_interval(cache: dict):
    """强制请求间隔，避免短时间内频繁调用"""
    if cache.get('last_call_time'):
        try:
            last_time = datetime.fromisoformat(cache['last_call_time'])
            elapsed = (datetime.now() - last_time).total_seconds()
            wait_time = REQUEST_INTERVAL - elapsed
            
            if wait_time > 0:
                print(f"⏳ 距离上次调用仅 {elapsed:.0f} 秒，需等待 {wait_time:.0f} 秒...")
                time.sleep(wait_time)
                print(f"✅ 等待结束，继续执行")
        except (ValueError, TypeError):
            pass


# ========== 启动去重检查 ==========

def check_today_data() -> bool:
    """
    检查今天是否已有数据
    返回 True 表示今天已有数据，应跳过 API 调用
    """
    today = datetime.now().date().isoformat()
    try:
        result = supabase.table('douban_comments') \
            .select('id', count='exact') \
            .eq('crawl_date', today) \
            .execute()
        
        count = result.count if hasattr(result, 'count') else len(result.data)
        print(f"📋 今日 ({today}) 数据库中已有 {count} 条评论")
        
        if count > 0:
            print(f"✅ 今日已有数据，无需重复抓取，直接退出")
            print(f"   如需重新抓取，请手动在 Supabase 中删除今日数据")
            return True
        
        return False
    except Exception as e:
        print(f"⚠️ 查询今日数据失败: {e}，将继续执行")
        return False


# ========== 工具函数 ==========

def get_text_hash(text: str) -> str:
    """计算文本的 MD5 hash 用于去重"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def get_existing_hashes() -> set:
    """获取数据库中已存在的所有评论 hash"""
    existing_hashes = set()
    try:
        offset = 0
        limit = 1000
        while True:
            result = supabase.table('douban_comments') \
                .select('comment_text') \
                .range(offset, offset + limit - 1) \
                .execute()
            
            if not result.data:
                break
                
            for row in result.data:
                if row.get('comment_text'):
                    existing_hashes.add(get_text_hash(row['comment_text']))
            
            if len(result.data) < limit:
                break
            offset += limit
            
        print(f"📦 数据库中已有 {len(existing_hashes)} 条评论 hash")
    except Exception as e:
        print(f"⚠️ 获取已有 hash 失败: {e}")
    return existing_hashes


# ========== 获取影评数据 ==========

def fetch_reviews_page(start: int = 0) -> list:
    """获取单页影评数据"""
    url = "https://api.justoneapi.com/api/douban/get-movie-reviews/v1"
    params = {
        "token": TOKEN,
        "subjectId": SUBJECT_ID,
        "start": start,
        "count": PAGE_SIZE
    }
    
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"❌ 请求失败: {resp.status_code}, start={start}")
            return []
        
        data = resp.json()
        if data.get("code") != 0:
            print(f"❌ 接口返回错误: {data.get('message', '未知错误')}, start={start}")
            return []
        
        reviews = data.get("data", {}).get("reviews", [])
        return reviews
    except Exception as e:
        print(f"❌ 请求异常: {e}, start={start}")
        return []

def fetch_all_reviews(cache: dict) -> list:
    """
    循环获取所有影评，最多 MAX_REVIEWS 条
    包含请求间隔控制和费用预警
    """
    all_reviews = []
    start = 0
    page_num = 0
    
    print(f"🌐 开始抓取影评，每页 {PAGE_SIZE} 条，最多 {MAX_REVIEWS} 条...")
    
    while len(all_reviews) < MAX_REVIEWS:
        page_num += 1
        
        # 检查调用次数限制
        if cache['call_count'] >= DAILY_CALL_LIMIT:
            print(f"🚫 已达每日调用上限，停止抓取")
            break
        
        # 强制请求间隔（第一次除外）
        if page_num > 1:
            enforce_request_interval(cache)
        
        print(f"  📄 正在获取第 {page_num} 页 (start={start})...")
        reviews = fetch_reviews_page(start)
        
        # 记录 API 调用
        record_api_call(cache)
        
        if not reviews:
            print(f"  ⚠️ 第 {page_num} 页无数据，停止抓取")
            break
        
        all_reviews.extend(reviews)
        print(f"  ✅ 获取到 {len(reviews)} 条，累计 {len(all_reviews)} 条")
        
        # 如果返回数量少于 PAGE_SIZE，说明没有更多数据了
        if len(reviews) < PAGE_SIZE:
            print("  ℹ️ 返回数据少于每页数量，停止抓取")
            break
        
        start += PAGE_SIZE
        
        # 达到上限时截断
        if len(all_reviews) > MAX_REVIEWS:
            all_reviews = all_reviews[:MAX_REVIEWS]
            print(f"  ℹ️ 已达到上限 {MAX_REVIEWS} 条，停止抓取")
            break
    
    return all_reviews


# ========== 存入 Supabase ==========

def save_reviews(reviews: list) -> int:
    """存入数据库，根据 comment_text hash 去重"""
    today = datetime.now().date().isoformat()
    
    existing_hashes = get_existing_hashes()
    
    success_count = 0
    skip_count = 0
    
    for review in reviews:
        comment_text = review.get('description', '') or review.get('title', '')
        if not comment_text:
            continue
        
        text_hash = get_text_hash(comment_text)
        if text_hash in existing_hashes:
            skip_count += 1
            continue
        
        data = {
            "comment_text": comment_text[:2000],
            "crawl_date": today,
            "comment_source": "api",
            "movie_name": MOVIE_NAME,
            "rating_text": review.get('rating', ''),
            "review_title": review.get('title', '')[:200],
            "author": review.get('username', ''),
            "review_time": review.get('time', '')
        }
        
        try:
            supabase.table('douban_comments').insert(data).execute()
            success_count += 1
            existing_hashes.add(text_hash)
        except Exception as e:
            print(f"  ❌ 插入失败: {e}")
    
    print(f"\n✅ 成功存入 {success_count} 条影评，跳过 {skip_count} 条重复")
    return success_count


# ========== 主函数 ==========

def main():
    print("=" * 55)
    print("  豆瓣影评抓取工具（含费用控制）")
    print(f"  电影: {MOVIE_NAME}")
    print(f"  Subject ID: {SUBJECT_ID}")
    print(f"  每日调用上限: {DAILY_CALL_LIMIT} 次")
    print(f"  请求间隔: {REQUEST_INTERVAL} 秒")
    print("=" * 55)
    
    # 第一步：检查今天是否已有数据（启动去重）
    print("\n【步骤1】检查今日数据...")
    if check_today_data():
        return
    
    # 第二步：检查今日调用次数限制
    print("\n【步骤2】检查 API 调用次数...")
    cache = check_daily_limit()
    if cache['call_count'] >= DAILY_CALL_LIMIT:
        print("💡 提示：今日调用次数已用完，明日自动重置")
        return
    
    # 第三步：抓取影评
    print("\n【步骤3】开始抓取影评...")
    reviews = fetch_all_reviews(cache)
    
    if reviews:
        print(f"\n【步骤4】共获取到 {len(reviews)} 条影评，开始存入数据库...")
        save_reviews(reviews)
    else:
        print("\n❌ 没有获取到影评数据")
    
    # 汇总
    print("\n" + "=" * 55)
    print(f"  今日 API 调用: {cache['call_count']}/{DAILY_CALL_LIMIT} 次")
    print(f"  费用估算: {cache['call_count'] * 0.1:.1f} 元")
    print("=" * 55)

if __name__ == "__main__":
    main()
