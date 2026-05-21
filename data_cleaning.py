#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据清洗脚本 - 检查并修复所有Supabase表的数据质量问题
"""
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict

HEADERS = {
    "apikey": "sb_publishable_36nGYLplp0DYcGbTx6GWpA_K11Jb9Gd",
    "Authorization": "Bearer sb_publishable_36nGYLplp0DYcGbTx6GWpA_K11Jb9Gd",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}
BASE_URL = "https://ebmncqnzammtplpwlveb.supabase.co/rest/v1"


def fetch_table(table_name, order=None):
    url = f"{BASE_URL}/{table_name}?limit=1000"
    if order:
        url += f"&order={order}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def update_row(table_name, row_id, data):
    url = f"{BASE_URL}/{table_name}?id=eq.{row_id}"
    resp = requests.patch(url, headers=HEADERS, json=data)
    return resp


def insert_row(table_name, data):
    url = f"{BASE_URL}/{table_name}"
    resp = requests.post(url, headers=HEADERS, json=data, timeout=30)
    return resp


def delete_row(table_name, row_id):
    url = f"{BASE_URL}/{table_name}?id=eq.{row_id}"
    resp = requests.delete(url, headers=HEADERS)
    return resp


def clean_movie_daily_stats():
    """清洗每日票房数据表"""
    print("\n" + "=" * 60)
    print("【清洗 movie_daily_stats 表】")
    print("=" * 60)

    data = fetch_table("movie_daily_stats", order="stat_date.asc")

    # 1. 检测全部为NULL的列
    all_keys = set()
    for row in data:
        all_keys.update(row.keys())

    null_cols = []
    for key in all_keys:
        if all(row.get(key) is None for row in data):
            null_cols.append(key)

    print(f"全部为NULL的列 ({len(null_cols)}): {null_cols}")

    # 2. 检测异常值: daily_box < 0 或 total_box 不递增
    issues = []
    prev_total = None
    for i, row in enumerate(data):
        # 检查daily_box异常
        db = row.get("daily_box")
        if db is not None and db < 0:
            issues.append(f"行{row['id']} {row['stat_date']}: daily_box为负值 {db}")

        # 检查total_box是否单调递增
        tb = row.get("total_box")
        if tb is not None:
            if prev_total is not None and tb < prev_total:
                issues.append(
                    f"行{row['id']} {row['stat_date']}: total_box={tb} < 前一天={prev_total}"
                )
            prev_total = tb

    # 3. 验证 total_box = 首日预售 + 累计daily_box
    PRE_SALE = 1342.8
    running = PRE_SALE
    for row in data:
        if row.get("daily_box") is not None:
            running += row["daily_box"]
            expected = round(running, 2)
            actual = row.get("total_box")
            if actual is not None and abs(expected - actual) > 1.0:
                issues.append(
                    f"行{row['id']} {row['stat_date']}: 累计计算={expected}, 实际total_box={actual}"
                )

    # 4. 清洗空rating_text
    print("\n数据质量报告:")
    print(f"  总行数: {len(data)}")
    print(f"  全NULL列: {null_cols}")
    print(f"  数据异常: {len(issues)} 个")
    for iss in issues:
        print(f"    ⚠ {iss}")

    if issues:
        print("\n发现异常，开始修复...")
        # 按日期重新计算total_box
        running = 1342.8
        for row in data:
            if row.get("daily_box") is not None:
                running += row["daily_box"]
                correct_total = round(running, 2)
                actual = row.get("total_box")
                if actual is not None and abs(correct_total - actual) > 0.5:
                    update_row("movie_daily_stats", row["id"],
                               {"total_box": correct_total})
                    print(f"  修复: {row['stat_date']} total_box {actual} → {correct_total}")

    print("\nmovie_daily_stats 清洗完成!")

    # 统计摘要
    valid_rows = [r for r in data if r.get("daily_box") is not None]
    if valid_rows:
        print(f"  票房日期范围: {valid_rows[0]['stat_date']} ~ {valid_rows[-1]['stat_date']}")
        print(f"  日票房范围: {min(r['daily_box'] for r in valid_rows):.2f} ~ "
              f"{max(r['daily_box'] for r in valid_rows):.2f} 万元")
        print(f"  累计票房: {valid_rows[-1]['total_box']:.2f} 万元")

    return data


def clean_movie_realtime():
    """清洗实时评分数据表"""
    print("\n" + "=" * 60)
    print("【清洗 movie_realtime 表】")
    print("=" * 60)

    data = fetch_table("movie_realtime")

    # 1. 检测全NULL列
    all_keys = set()
    for row in data:
        all_keys.update(row.keys())

    null_cols = [k for k in all_keys if all(row.get(k) is None for row in data)
                 if k not in ("id",)]

    print(f"全部为NULL的列: {null_cols}")

    # 2. 补全 crawl_date: 从 crawl_time 提取
    for row in data:
        if row.get("crawl_date") is None and row.get("crawl_time"):
            ct = row["crawl_time"]
            date_str = ct[:10] if "T" in ct else ct[:10]
            update_row("movie_realtime", row["id"], {"crawl_date": date_str})
            print(f"  补全: id={row['id']} crawl_date={date_str}")

    # 3. 检测评分异常
    for row in data:
        score = row.get("douban_score")
        if score is not None and (score < 0 or score > 10):
            print(f"  异常评分: id={row['id']} score={score}")

        votes = row.get("douban_votes")
        if votes is not None and votes < 0:
            print(f"  异常评分人数: id={row['id']} votes={votes}")

    # 4. 删除重复: 同一 crawl_date 保留最新
    date_rows = defaultdict(list)
    for row in data:
        cd = row.get("crawl_date", "")
        if cd:
            date_rows[cd].append(row)

    for date_str, rows in date_rows.items():
        if len(rows) > 1:
            # 按crawl_time排序, 保留最新的
            rows.sort(key=lambda r: r.get("crawl_time", ""), reverse=True)
            for dup_row in rows[1:]:
                delete_row("movie_realtime", dup_row["id"])
                print(f"  删除重复: id={dup_row['id']} crawl_date={date_str}")

    print(f"\nmovie_realtime 清洗完成! 总行数: {len(data)}")
    return data


def clean_douban_comments():
    """清洗豆瓣评论数据表"""
    print("\n" + "=" * 60)
    print("【清洗 douban_comments 表】")
    print("=" * 60)

    data = fetch_table("douban_comments")

    # 1. 检测全NULL列
    all_keys = set()
    for row in data:
        all_keys.update(row.keys())

    null_cols = [k for k in all_keys if all(row.get(k) is None for row in data)
                 if k not in ("id",)]
    print(f"全部为NULL的列: {null_cols}")

    # 2. 统计评分分布
    rating_dist = defaultdict(int)
    for row in data:
        rt = row.get("rating_text", "")
        rating_dist[rt if rt else "(空)"] += 1

    print("\n评分分布:")
    for k, v in sorted(rating_dist.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v} ({v/len(data)*100:.1f}%)")

    # 3. 标准化空rating_text: 保持为空，标记数据质量
    empty_ratings = [r for r in data if not r.get("rating_text")]
    print(f"\n空评分评论: {len(empty_ratings)} 条")
    for r in empty_ratings[:3]:
        print(f"  id={r['id']}: {r.get('review_title', '')[:50]}")

    # 4. 检测过长评论
    for row in data:
        ct = row.get("comment_text", "")
        if len(ct) > 3000:
            print(f"  过长评论: id={row['id']} 长度={len(ct)}")

    print(f"\ndouban_comments 清洗完成! 总行数: {len(data)}")
    return data


def generate_report():
    """生成数据清洗总报告"""
    print("\n" + "=" * 60)
    print("         数据清洗总报告")
    print("=" * 60)

    tables = {
        "movie_daily_stats": fetch_table("movie_daily_stats"),
        "movie_realtime": fetch_table("movie_realtime"),
        "douban_comments": fetch_table("douban_comments"),
    }

    for name, data in tables.items():
        print(f"\n📊 {name}: {len(data)} 行")

        if name == "movie_daily_stats":
            valid = [r for r in data if r.get("daily_box") is not None]
            if valid:
                print(f"   日期: {valid[0]['stat_date']} ~ {valid[-1]['stat_date']}")
                print(f"   最新日票房: {valid[-1]['daily_box']:.2f} 万元")
                print(f"   最新累计票房: {valid[-1]['total_box']:.2f} 万元")
                print(f"   平均日票房: {sum(r['daily_box'] for r in valid)/len(valid):.0f} 万元")

        elif name == "movie_realtime":
            latest = max(data, key=lambda r: r.get("crawl_time", "")) if data else None
            if latest:
                print(f"   最新评分: {latest.get('douban_score')}")
                print(f"   最新评分人数: {latest.get('douban_votes'):,}")

        elif name == "douban_comments":
            ratings = [r.get("rating_text") for r in data if r.get("rating_text")]
            pos = sum(1 for r in ratings if r in ("力荐", "推荐"))
            print(f"   总评论: {len(data)}")
            print(f"   有评分: {len(ratings)}")
            print(f"   好评率: {pos/len(ratings)*100:.1f}%" if ratings else "   好评率: N/A")


if __name__ == "__main__":
    print("开始数据清洗...")
    print(f"数据库: {BASE_URL}")

    clean_movie_daily_stats()
    clean_movie_realtime()
    clean_douban_comments()
    generate_report()

    print("\n✅ 数据清洗全部完成!")
