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
MOVIE_DETAIL_URL = "https://piaofang.maoyan.com/movie/1516982"
TARGET_MOVIE = "给阿嬷的情书"
DAYS_TO_FETCH = 30
PRE_SALE_BOX = 1342.8  # 预售票房基准值（万元）

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
                json={"url": url, "format": "raw"},
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


def fetch_detail_page_data() -> List[Dict]:
    """从电影详情页提取历史票房数据"""
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

    total_box = PRE_SALE_BOX
    result = []
    for i in range(len(dates)):
        val = None
        if i < len(real_values) and real_values[i]:
            try:
                val = float(real_values[i])
                total_box += val
            except ValueError:
                pass
        if val is not None:
            result.append({
                'stat_date': dates[i],
                'daily_box': val,
                'total_box': round(total_box, 2),
            })

    logger.info(f"[详情页] 解析到 {len(result)} 条有效数据")
    if result:
        # 验证最后一条
        last = result[-1]
        logger.info(f"  最新: {last['stat_date']} 日票房={last['daily_box']:.2f}万 累计={last['total_box']:.2f}万")
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

    storage = DataStorage()
    records = fetch_detail_page_data()

    if not records:
        logger.error("未获取到数据，终止")
        sys.exit(1)

    success = 0
    for rec in records:
        if storage.save(rec):
            success += 1
        time.sleep(0.3)  # 避免请求过快

    logger.info(f"\n✅ 完成: {success}/{len(records)} 条数据保存成功")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n用户中断")
        sys.exit(130)
    except Exception as e:
        logger.error(f"异常: {e}", exc_info=True)
        sys.exit(1)
