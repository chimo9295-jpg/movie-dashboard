#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
猫眼电影历史票房数据爬虫（优化版）
从电影详情页提取历史每日票房（日票房 + 累计票房）
已移除：无效的API接口调用（排片占比、上座率等始终为NULL）
"""
import os
import re
import sys
import json
import time
import logging
from datetime import datetime, timedelta

import requests
from typing import List, Dict, Optional

from bs4 import BeautifulSoup
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

BRIGHTDATA_TOKEN = os.environ.get("BRIGHTDATA_TOKEN", "")
BRIGHTDATA_API_URL = os.environ.get("BRIGHTDATA_API_URL", "https://api.brightdata.com/request")
BRIGHTDATA_ZONE = os.environ.get("BRIGHTDATA_ZONE", "cli_unlocker")
MOVIE_DETAIL_URL = "https://piaofang.maoyan.com/movie/1516982"
DASHBOARD_URL = "https://piaofang.maoyan.com/dashboard-ajax"
TARGET_MOVIE = "给阿嬷的情书"
DAYS_TO_FETCH = 30
PRE_SALE_BOX_FALLBACK = 1342.8  # 预售票房回退值（无 dashboard 数据时使用）


def parse_box_desc(desc: str) -> float:
    """解析票房描述文字 → 万元  例: '6.16亿'→61600"""
    if not desc:
        return 0.0
    try:
        desc = desc.strip()
        if '亿' in desc:
            return float(desc.replace('亿', '')) * 10000
        elif '万' in desc:
            return float(desc.replace('万', ''))
        else:
            return float(desc)
    except ValueError:
        return 0.0


def fetch_dashboard_box(movie_id: int) -> tuple:
    """从 dashboard-ajax 获取指定电影的综合/分账累计票房
    返回: (total_box_w, split_box_w, pre_sale_w, service_fee_rate)
    """
    raw = brightdata_fetch(DASHBOARD_URL, timeout=120)
    if not raw:
        return 0, 0, PRE_SALE_BOX_FALLBACK, 0.0
    try:
        data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode('utf-8'))
        movie_list = data.get('movieList', {}).get('data', {}).get('list', [])
        for m in movie_list:
            mi = m.get('movieInfo', {})
            if mi.get('movieId') == movie_id:
                total_desc = m.get('sumBoxDesc', '')
                split_desc = m.get('sumSplitBoxDesc', '')
                total_w = parse_box_desc(total_desc)
                split_w = parse_box_desc(split_desc)
                if split_w > 0 and total_w > 0:
                    service_fee_rate = round((total_w - split_w) / split_w, 6)
                else:
                    service_fee_rate = 0.0
                logger.info(f"[Dashboard] total={total_desc}({total_w}w) split={split_desc}({split_w}w) fee_rate={service_fee_rate*100:.2f}%")
                return total_w, split_w, 0.0, service_fee_rate
    except Exception as e:
        logger.error(f"[Dashboard] 解析失败: {e}")
    return 0, 0, PRE_SALE_BOX_FALLBACK, 0.0

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ebmncqnzammtplpwlveb.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_36nGYLplp0DYcGbTx6GWpA_K11Jb9Gd")


def brightdata_fetch(url: str, timeout: int = 120, max_retries: int = 3) -> Optional[str]:
    token = BRIGHTDATA_TOKEN or os.environ.get("BRIGHTDATA_TOKEN", "")
    if not token:
        logger.error("[BrightData] BRIGHTDATA_TOKEN 未设置")
        return None

    for attempt in range(max_retries):
        if attempt > 0:
            logger.info(f"[BrightData] 重试 {attempt + 1}/{max_retries}...")
            time.sleep(5 * attempt)

        logger.info(f"[BrightData] 抓取: {url}")
        try:
            resp = requests.post(
                BRIGHTDATA_API_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"zone": BRIGHTDATA_ZONE, "url": url, "format": "raw"},
                timeout=timeout,
            )
            if resp.status_code != 200:
                logger.error(f"[BrightData] API 返回 {resp.status_code}: {resp.text[:200]}")
                continue
            raw = resp.text.strip()
            if not raw or len(raw) < 10 or raw == "Not Found":
                logger.warning(f"[BrightData] 内容无效 ({len(raw) if raw else 0}字符)")
                continue
            logger.info(f"[BrightData] 成功, 长度: {len(raw)}")
            return raw
        except requests.exceptions.Timeout:
            logger.error("[BrightData] 超时")
            continue
        except requests.exceptions.RequestException as e:
            logger.error(f"[BrightData] 异常: {e}")
            continue

    logger.error("[BrightData] 全部重试失败")
    return None


def fetch_detail_page_data(dashboard_split_total: float = 0.0,
                           service_fee_rate: float = 0.0) -> List[Dict]:
    """从电影详情页提取历史票房数据，应用预售和服务费率
    - daily_box = 分账日票房 × (1 + service_fee_rate)
    - total_box = (预售 + 累计分账) × (1 + service_fee_rate)
    - 预售 = dashboard分账累计 - 详情页原始分账累计
    """
    raw = brightdata_fetch(MOVIE_DETAIL_URL)
    if not raw:
        return []

    soup = BeautifulSoup(raw, 'html.parser')
    json_data = None

    for script in soup.find_all('script', {'type': 'application/json'}):
        try:
            d = json.loads(script.string)
            if isinstance(d, dict) and ('boxshowChartData' in d or 'movieId' in d):
                json_data = d
                break
        except (json.JSONDecodeError, TypeError):
            continue

    if not json_data:
        logger.error("[详情页] 未提取到JSON数据")
        return []

    box_data = json_data.get('boxshowChartData', {}).get('chartData', {}).get('box', {})
    if not box_data:
        logger.error("[详情页] 未找到票房图表数据")
        return []

    dates = box_data.get('date', [])
    real_values = box_data.get('real', [])
    if not dates:
        logger.error("[详情页] 日期列表为空")
        return []

    logger.info(f"[详情页] 获取到 {len(dates)} 天数据: {dates[0]} ~ {dates[-1]}")

    # 先算原始分账累计（不含预售、不含服务费）
    raw_split_total = 0.0
    raw_records = []
    for i in range(len(dates)):
        val = None
        if i < len(real_values) and real_values[i]:
            try:
                val = float(real_values[i])
                raw_split_total += val
            except ValueError:
                pass
        if val is not None:
            raw_records.append({'date': dates[i], 'daily_split': val})

    # 动态计算预售：用 dashboard 分账累计 - 详情页分账累计
    if dashboard_split_total > 0 and raw_split_total > 0:
        pre_sale_box = max(0, round(dashboard_split_total - raw_split_total, 2))
        logger.info(f"[详情页] 动态预售: {dashboard_split_total}w - {raw_split_total:.2f}w = {pre_sale_box}w")
    else:
        pre_sale_box = PRE_SALE_BOX_FALLBACK
        logger.info(f"[详情页] 使用回退预售: {PRE_SALE_BOX_FALLBACK}w (无 dashboard 数据)")

    fee_mult = 1.0 + service_fee_rate
    split_total = pre_sale_box
    result = []
    for rec in raw_records:
        split_total += rec['daily_split']
        daily_total = round(rec['daily_split'] * fee_mult, 2)
        total_box_val = round(split_total * fee_mult, 2)
        result.append({
            'stat_date': rec['date'],
            'daily_box': daily_total,
            'total_box': total_box_val,
        })

    logger.info(f"[详情页] 解析到 {len(result)} 条有效数据 (服务费率={service_fee_rate*100:.2f}%, 预售={pre_sale_box}w)")
    if result:
        last = result[-1]
        logger.info(f"  最新: {last['stat_date']} 日票房={last['daily_box']:.2f}万 累计={last['total_box']:.2f}万 = {last['total_box']/10000:.2f}亿")
    return result


class DataStorage:
    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("[Storage] Supabase连接成功")

    def save(self, data: Dict) -> bool:
        try:
            stat_date = data['stat_date']
            record = {
                'movie_id': 1516982,
                'stat_date': stat_date,
                'daily_box': data['daily_box'],
                'total_box': data['total_box'],
                'crawl_time': datetime.now().isoformat(),
            }

            existing = self.client.table('mayday_daily_stats') \
                .select('id').eq('movie_id', 1516982).eq('stat_date', stat_date).execute()

            if existing.data:
                rid = existing.data[0]['id']
                self.client.table('mayday_daily_stats').update(record).eq('id', rid).execute()
                logger.info(f"  [Storage] 更新: {stat_date} daily={data['daily_box']:.2f}万 total={data['total_box']:.2f}万")
            else:
                self.client.table('mayday_daily_stats').insert(record).execute()
                logger.info(f"  [Storage] 插入: {stat_date} daily={data['daily_box']:.2f}万 total={data['total_box']:.2f}万")
            return True
        except Exception as e:
            logger.error(f"  [Storage] 失败 {data.get('stat_date')}: {e}")
            return False


def main():
    logger.info("=" * 50)
    logger.info(f"猫眼票房爬虫 - {TARGET_MOVIE}")
    logger.info("=" * 50)

    # 1. 获取 dashboard 数据（服务费率、分账累计）
    total_w, split_w, _, service_fee_rate = fetch_dashboard_box(1516982)

    # 2. 获取详情页数据并应用服务费率
    storage = DataStorage()
    records = fetch_detail_page_data(dashboard_split_total=split_w,
                                     service_fee_rate=service_fee_rate)

    if not records:
        logger.error("未获取到数据，终止")
        sys.exit(1)

    success = 0
    for rec in records:
        if storage.save(rec):
            success += 1
        time.sleep(0.3)  # 避免请求过快

    logger.info(f"\n[完成] {success}/{len(records)} 条数据保存成功")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n用户中断")
        sys.exit(130)
    except Exception as e:
        logger.error(f"异常: {e}", exc_info=True)
        sys.exit(1)
