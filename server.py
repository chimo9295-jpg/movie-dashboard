#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电影数据看板后端服务
- Supabase数据查询
- 滚动窗口票房预测
- ChatBI自然语言分析 (DeepSeek API)
"""
import json
import sqlite3
import os
import sys
import requests
import numpy as np
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

SUPABASE_URL = "https://ebmncqnzammtplpwlveb.supabase.co"
SUPABASE_KEY = "sb_publishable_36nGYLplp0DYcGbTx6GWpA_K11Jb9Gd"
SUPABASE_REST = f"{SUPABASE_URL}/rest/v1"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

# 内存数据库 (ChatBI用)
sqlite_conn = sqlite3.connect(":memory:", check_same_thread=False)
sqlite_conn.row_factory = sqlite3.Row


def fetch_supabase(table, select="*", order=None, limit=1000):
    url = f"{SUPABASE_REST}/{table}?limit={limit}"
    if select != "*":
        url += f"&select={select}"
    if order:
        url += f"&order={order}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def init_sqlite():
    """初始化SQLite内存数据库供ChatBI使用"""
    c = sqlite_conn.cursor()

    daily = fetch_supabase("movie_daily_stats", order="stat_date.asc")
    c.execute("""CREATE TABLE IF NOT EXISTS movie_daily_stats (
        id INTEGER, stat_date TEXT, daily_box REAL, total_box REAL,
        movie_name TEXT, crawl_time TEXT
    )""")
    for r in daily:
        c.execute("INSERT INTO movie_daily_stats VALUES(?,?,?,?,?,?)",
                  (r.get("id"), r.get("stat_date"), r.get("daily_box"),
                   r.get("total_box"), r.get("movie_name"), r.get("crawl_time")))

    realtime = fetch_supabase("movie_realtime")
    c.execute("""CREATE TABLE IF NOT EXISTS movie_realtime (
        id INTEGER, movie_name TEXT, douban_score REAL, douban_votes INTEGER,
        crawl_time TEXT, crawl_date TEXT
    )""")
    for r in realtime:
        c.execute("INSERT INTO movie_realtime VALUES(?,?,?,?,?,?)",
                  (r.get("id"), r.get("movie_name"), r.get("douban_score"),
                   r.get("douban_votes"), r.get("crawl_time"), r.get("crawl_date")))

    comments = fetch_supabase("douban_comments")
    c.execute("""CREATE TABLE IF NOT EXISTS douban_comments (
        id INTEGER, comment_text TEXT, crawl_date TEXT, rating_text TEXT,
        review_title TEXT, author TEXT, review_time TEXT, movie_name TEXT
    )""")
    for r in comments:
        c.execute("INSERT INTO douban_comments VALUES(?,?,?,?,?,?,?,?)",
                  (r.get("id"), r.get("comment_text"), r.get("crawl_date"),
                   r.get("rating_text"), r.get("review_title"), r.get("author"),
                   r.get("review_time"), r.get("movie_name")))

    sqlite_conn.commit()
    print(f"[SQLite] 已加载 {len(daily)} daily + {len(realtime)} realtime + {len(comments)} comments")


# ──────────────── 滚动窗口预测 ────────────────

def rolling_window_predict(data, window=5, forecast_days=7):
    """
    滚动窗口预测: 用最近window天移动平均预测未来
    data: [(date, value), ...] 按日期排序
    返回: [(date, predicted_value), ...]
    """
    dates = [d[0] for d in data]
    values = np.array([d[1] for d in data], dtype=float)

    predictions = []
    for i in range(forecast_days):
        # 使用最近window个真实值+已预测值的加权平均
        recent = values[-window:] if len(values) >= window else values
        # 指数衰减权重
        weights = np.exp(np.linspace(-1, 0, len(recent)))
        weights = weights / weights.sum()
        pred = np.dot(recent, weights)

        # 周末/节假日调整因子
        last_date = datetime.strptime(dates[-1] if dates else "2026-05-18", "%Y-%m-%d")
        next_date = last_date + timedelta(days=i + 1)
        weekday_factor = 1.15 if next_date.weekday() >= 5 else 0.92  # 周末高于工作日

        pred_adj = pred * weekday_factor
        predictions.append((next_date.strftime("%Y-%m-%d"), round(pred_adj, 2)))

        # 将预测值加入序列用于下一步预测
        values = np.append(values, pred_adj)
        dates.append(next_date.strftime("%Y-%m-%d"))

    return predictions


def calculate_prediction(daily_data):
    """计算完整预测"""
    sorted_data = sorted(
        [(d["stat_date"], d["daily_box"]) for d in daily_data if d.get("daily_box")],
        key=lambda x: x[0]
    )
    if len(sorted_data) < 5:
        return {"error": "历史数据不足（需至少5天）"}

    predictions = rolling_window_predict(sorted_data, window=5, forecast_days=7)

    # 计算累计票房预测
    last_total = daily_data[-1].get("total_box", 0) if daily_data else 0
    total_preds = []
    running_total = last_total
    for d, v in predictions:
        running_total += v
        total_preds.append({"date": d, "daily_pred": v, "total_pred": round(running_total, 2)})

    return {
        "predictions": [{"date": d, "daily_pred": v} for d, v in predictions],
        "total_predictions": total_preds
    }


# ──────────────── ChatBI 模块 ────────────────

def build_schema_prompt():
    return """数据库有以下表（只有一个电影《给阿嬷的情书》的数据，查询时不需要WHERE过滤movie_name）：

## movie_daily_stats - 每日票房数据 (19行, 2026-04-30~2026-05-18)
字段: stat_date(日期), daily_box(日票房万元), total_box(累计票房万元)

## movie_realtime - 豆瓣评分追踪 (9行)
字段: douban_score(豆瓣评分0-10), douban_votes(评分人数), crawl_date(日期)

## douban_comments - 影评数据 (56行)
字段: rating_text(评分:力荐=好评/推荐=好评/还行=中评/很差=差评/空=未评分), comment_text(影评内容), author(作者), review_title(标题), review_time(评论时间)

数据库是SQLite。查询时不要加movie_name条件，直接查全表即可。

必须只返回一行JSON(不含markdown): {"sql": "SQL语句", "explanation": "简短中文解释", "chart_type": "bar/line/pie/table/text", "chart_config": {"title": "标题", "x_label": "X轴", "y_label": "Y轴"}}"""


def chatbi_analyze(question, history):
    if not DEEPSEEK_API_KEY:
        return local_chatbi(question)

    messages = [{"role": "system", "content": build_schema_prompt()}]
    for h in history[-6:]:
        messages.append(h)
    messages.append({"role": "user", "content": question})

    try:
        resp = requests.post(DEEPSEEK_API_URL, headers={
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }, json={
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 2000,
        }, timeout=30)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else parts[0]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        return json.loads(content)
    except Exception as e:
        print(f"[ChatBI] DeepSeek错误: {e}")
        return local_chatbi(question)


def local_chatbi(question):
    """本地规则匹配回退"""
    q = question

    if any(w in q for w in ["好评", "差评", "中评", "占比", "评分分布", "比例"]):
        return {
            "sql": "SELECT CASE WHEN rating_text='力荐' THEN '好评(力荐)' WHEN rating_text='推荐' THEN '好评(推荐)' WHEN rating_text='还行' THEN '中评(还行)' WHEN rating_text='很差' THEN '差评(很差)' ELSE '未评分' END AS 分类, COUNT(*) AS 数量, ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM douban_comments),1) AS 百分比 FROM douban_comments GROUP BY 分类 ORDER BY 数量 DESC",
            "explanation": "统计影评中各评分等级的分布和占比。",
            "chart_type": "pie",
            "chart_config": {"title": "影评评分分布", "x_label": "", "y_label": ""}
        }
    if any(w in q for w in ["评分趋势", "评分变化", "分数走势", "豆瓣分"]):
        return {
            "sql": "SELECT crawl_date AS 日期, douban_score AS 豆瓣评分, douban_votes AS 评分人数 FROM movie_realtime WHERE crawl_date IS NOT NULL ORDER BY crawl_date ASC",
            "explanation": "查询豆瓣评分和评分人数随日期的变化趋势。",
            "chart_type": "line",
            "chart_config": {"title": "豆瓣评分趋势", "x_label": "日期", "y_label": "豆瓣评分"}
        }
    if any(w in q for w in ["评分人数", "打分人数", "人数增长", "人数变化"]):
        return {
            "sql": "SELECT crawl_date AS 日期, douban_votes AS 评分人数 FROM movie_realtime WHERE crawl_date IS NOT NULL ORDER BY crawl_date ASC",
            "explanation": "查询豆瓣评分人数增长趋势。",
            "chart_type": "line",
            "chart_config": {"title": "评分人数增长趋势", "x_label": "日期", "y_label": "评分人数"}
        }
    if any(w in q for w in ["票房趋势", "每日票房", "日票房"]):
        return {
            "sql": "SELECT stat_date AS 日期, daily_box AS 日票房, total_box AS 累计票房 FROM movie_daily_stats ORDER BY stat_date ASC",
            "explanation": "查询每日票房和累计票房走势。",
            "chart_type": "line",
            "chart_config": {"title": "票房走势", "x_label": "日期", "y_label": "票房(万元)"}
        }
    if any(w in q for w in ["评论", "影评", "最新"]):
        return {
            "sql": "SELECT author AS 作者, rating_text AS 评分, review_title AS 标题, SUBSTR(comment_text,1,150) AS 摘要, review_time AS 时间 FROM douban_comments ORDER BY review_time DESC LIMIT 10",
            "explanation": "查询最新的影评列表。",
            "chart_type": "table",
            "chart_config": {"title": "最新影评"}
        }
    # 默认: 总览
    return {
        "sql": "SELECT stat_date AS 日期, daily_box AS 日票房, total_box AS 累计票房 FROM movie_daily_stats ORDER BY stat_date ASC",
        "explanation": f"我理解为: '{question}'。展示票房数据趋势。",
        "chart_type": "line",
        "chart_config": {"title": "票房数据趋势", "x_label": "日期", "y_label": "万元"}
    }


def execute_chatbi_sql(sql):
    try:
        c = sqlite_conn.cursor()
        c.execute(sql)
        rows = c.fetchall()
        cols = [desc[0] for desc in c.description] if c.description else []
        data = [dict(zip(cols, r)) for r in rows]
        return {"success": True, "columns": cols, "data": data, "row_count": len(data)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ──────────────── API 路由 ────────────────

@app.route("/api/data")
def api_data():
    """获取所有看板数据"""
    try:
        daily = fetch_supabase("movie_daily_stats", order="stat_date.asc")
        realtime_all = fetch_supabase("movie_realtime", order="crawl_time.asc")
        realtime = realtime_all[-1] if realtime_all else {}
        comments = fetch_supabase("douban_comments", order="review_time.desc", limit=50)

        # 计算预测
        pred_result = calculate_prediction(daily) if daily else {}

        # 汇总统计
        sorted_daily = sorted(daily, key=lambda x: x.get("stat_date", ""))
        latest = sorted_daily[-1] if sorted_daily else {}
        latest_rating = realtime  # already the last record

        # 评分分布
        rating_dist = {}
        for c in comments:
            rt = c.get("rating_text", "") or "未评分"
            rating_dist[rt] = rating_dist.get(rt, 0) + 1

        return jsonify({
            "daily": daily,
            "realtime": latest_rating,
            "realtime_all": realtime_all,
            "comments": comments,
            "prediction": pred_result,
            "stats": {
                "latest_daily_box": latest.get("daily_box"),
                "latest_total_box": latest.get("total_box"),
                "latest_date": latest.get("stat_date"),
                "douban_score": latest_rating.get("douban_score"),
                "douban_votes": latest_rating.get("douban_votes"),
                "total_comments": len(comments),
                "rating_distribution": rating_dist,
                "data_days": len(sorted_daily),
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/predict")
def api_predict():
    """获取票房预测数据"""
    daily = fetch_supabase("movie_daily_stats", order="stat_date.asc")
    pred_result = calculate_prediction(daily)
    return jsonify(pred_result)


@app.route("/api/chatbi", methods=["POST"])
def api_chatbi():
    """ChatBI自然语言查询"""
    body = request.json
    question = body.get("question", "")
    history = body.get("history", [])

    if not question:
        return jsonify({"error": "问题不能为空"}), 400

    analysis = chatbi_analyze(question, history)
    sql = analysis.get("sql", "")
    result = execute_chatbi_sql(sql)

    return jsonify({
        "question": question,
        "sql": sql,
        "explanation": analysis.get("explanation", ""),
        "chart_type": analysis.get("chart_type", "table"),
        "chart_config": analysis.get("chart_config", {}),
        "query_result": result,
    })


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


if __name__ == "__main__":
    init_sqlite()
    print("\n=== 电影数据看板服务已启动 ===")
    print("http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
