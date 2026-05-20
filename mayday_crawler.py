#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026 五一档电影数据爬虫
数据来源: 猫眼专业版 https://piaofang.maoyan.com
功能: 日票房 / 累计票房 / 上座率 / 排片占比 / 票房占比

============================================================
猫眼 signKey 签名规则分析
============================================================

1. 核心算法位于 JS bundle 的 veri.ts 模块
2. 签名参数:
   - method: 请求方法 (GET/POST)
   - timeStamp: 毫秒级时间戳
   - User-Agent: navigator.userAgent 的 Base64 编码
   - index: Math.floor(1000 * Math.random() + 1) → 1000~1999 随机数
   - channelId: 固定 40009 (PC端)
   - sVersion: 固定 2
   - key: 固定 'A013F70DB97834C0A5492378BD76C53A'

3. 签名字符串构造:
   按插入顺序拼接: method=GET&timeStamp=xxx&User-Agent=xxx&index=xxx&channelId=40009&sVersion=2&key=A013F70DB97834C0A5492378BD76C53A

4. signKey = MD5(签名字符串)

5. 最终请求参数 = 原始 query + {timeStamp, User-Agent, index, channelId, sVersion, signKey}
   (移除 method 和 key)

6. endpoint 响应中的数字使用自定义 woff 字体编码（防爬虫），需要解析字体文件解码

============================================================
API 端点说明
============================================================
- GET /dashboard-ajax          → 电影列表 + 实时数据 (部分数字字体编码)
- GET /movie/{movieId}         → 电影详情页 (内嵌 JSON，数字为明文)
- POST /dashboard-ajax/movie   → 需要 signKey 签名
- GET /dashboard/ajax-moviedetail → 电影详情数据
============================================================
"""

import os
import sys
import json
import time
import hashlib
import base64
import random
import logging
import re
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple
from io import BytesIO

import requests
from bs4 import BeautifulSoup
from fontTools.ttLib import TTFont
from supabase import create_client, Client

# ============================================================
# 配置
# ============================================================
BRIGHTDATA_TOKEN = os.environ.get("BRIGHTDATA_TOKEN", "")
BRIGHTDATA_API_URL = os.environ.get("BRIGHTDATA_API_URL", "https://api.brightdata.com/request")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ebmncqnzammtplpwlveb.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_36nGYLplp0DYcGbTx6GWpA_K11Jb9Gd")

# 五一档时间范围
MAYDAY_START = "2026-04-30"
MAYDAY_END = "2026-05-05"

# 五一档电影 ID 列表 (从 dashboard-ajax 获取)
MAYDAY_MOVIE_IDS = [
    1516982,  # 给阿嬷的情书 (2026-04-30)
    1528954,  # 消失的人 (2026-05-01)
    1525209,  # 寒战1994 (2026-05-01)
    1528750,  # 穿普拉达的女王2 (2026-04-30)
]

# 请求控制
REQUEST_DELAY = 3         # 请求间隔（秒）
MAX_RETRIES = 3           # 最大重试次数
CRAWL_TIMEOUT = 120       # brightdata 超时（秒）

# ============================================================
# 日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


# ============================================================
# BrightData 抓取工具
# ============================================================
def brightdata_fetch(url: str, timeout: int = CRAWL_TIMEOUT) -> Optional[bytes]:
    """通过 BrightData HTTP API 抓取页面，返回原始字节"""
    token = BRIGHTDATA_TOKEN or os.environ.get("BRIGHTDATA_TOKEN", "")
    if not token:
        logger.error("BRIGHTDATA_TOKEN 未设置")
        return None

    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            wait = 5 * (attempt + 1)
            logger.info(f"  重试 {attempt + 1}/{MAX_RETRIES}，等待 {wait}s...")
            time.sleep(wait)

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
                logger.warning(f"  BrightData API 返回 {resp.status_code}: {resp.text[:200]}")
                continue
            raw = resp.content
            if not raw or len(raw) < 50:
                logger.warning(f"  内容过短 ({len(raw) if raw else 0} 字节)")
                continue
            return raw
        except requests.exceptions.Timeout:
            logger.warning("  BrightData API 超时")
        except requests.exceptions.RequestException as e:
            logger.error(f"  BrightData API 异常: {e}")

    logger.error(f"  全部重试失败: {url}")
    return None


# ============================================================
# 猫眼字体解码器
# ============================================================
class MaoyanFontDecoder:
    """解析猫眼自定义 woff 字体，将编码字符映射回真实数字"""

    def __init__(self):
        self.char_to_digit: Dict[str, str] = {}
        self._font_loaded = False

    def load_font(self, woff_url: str):
        """下载并解析 woff 字体文件"""
        try:
            full_url = "https:" + woff_url if woff_url.startswith("//") else woff_url
            logger.info(f"  下载字体: {full_url}")
            resp = requests.get(full_url, timeout=30)
            resp.raise_for_status()

            font = TTFont(BytesIO(resp.content))
            cmap = font.getBestCmap()  # {unicode_codepoint: glyph_name}

            # 猫眼字体的数字 glyph 命名模式: uniE000~uniE009 对应 0~9
            # 或者通过 glyph 的渲染顺序来判断
            digit_map = {}
            for codepoint, glyph_name in cmap.items():
                # 尝试从 glyph name 提取数字
                match = re.match(r'uni[0-9A-Fa-f]+', glyph_name)
                if match:
                    # 按 unicode 码点范围分组，找到数字映射
                    pass

            # 更可靠的方法: 扫描所有 glyph，通过比较找出数字
            # 猫眼通常使用 PUA (Private Use Area) 码点 U+E000~U+E009 或类似
            reverse_cmap = {}
            for cp, gn in cmap.items():
                reverse_cmap[gn] = cp

            # 方法1: 按 glyph 名称顺序 (digit.0, digit.1, ...)
            for i in range(10):
                for prefix in [f'digit.{i}', f'num_{i}', f'number.{i}']:
                    if prefix in reverse_cmap:
                        digit_map[chr(reverse_cmap[prefix])] = str(i)
                        break

            # 方法2: 按 unicode 码点偏移 (最常见)
            if not digit_map:
                # 找到最小的 PUA 码点，假设它对应 0
                pua_chars = [(cp, chr(cp)) for cp in cmap
                             if 0xE000 <= cp <= 0xF8FF]
                pua_chars.sort()
                if len(pua_chars) >= 10:
                    # 假设连续的10个 PUA 字符对应 0-9
                    base = pua_chars[0][0]
                    for offset, (cp, ch) in enumerate(pua_chars[:10]):
                        if cp == base + offset:
                            digit_map[ch] = str(offset)

            # 方法3: 通过 HTML entity 格式 &#xXXXX;
            if not digit_map:
                # 从常见的猫眼编码表映射
                # 已知编码范围通常在这些 PUA 区域
                for cp, ch in pua_chars:
                    hex_str = f"{cp:04x}"
                    # 猫眼常用编码: 末位对应数字
                    last_nibble = cp & 0xF
                    if last_nibble < 10:
                        digit_map[ch] = str(last_nibble)

            self.char_to_digit = digit_map
            self._font_loaded = True
            logger.info(f"  字体解析完成，映射 {len(digit_map)} 个字符")

        except Exception as e:
            logger.error(f"  字体解析失败: {e}")
            self._font_loaded = False

    def decode_html_entities(self, text: str) -> str:
        """解码 HTML 实体格式的数字: &#xed4f;&#xedba;..."""
        def replace_entity(m):
            code = int(m.group(1), 16)
            ch = chr(code)
            return self.char_to_digit.get(ch, m.group(0))
        return re.sub(r'&#x([0-9a-fA-F]+);', replace_entity, text)


# ============================================================
# 数据获取
# ============================================================
def fetch_dashboard_data() -> Optional[Dict]:
    """获取 dashboard-ajax 数据（电影列表 + 实时指标）"""
    logger.info("获取 dashboard-ajax 数据...")
    raw = brightdata_fetch("https://piaofang.maoyan.com/dashboard-ajax")
    if not raw:
        return None

    try:
        text = raw.decode('utf-8')
        data = json.loads(text)
        font_style = data.get('fontStyle', '')
        # 提取 woff URL
        woff_match = re.search(r'url\("([^"]+\.woff)"\)', font_style)
        woff_url = woff_match.group(1) if woff_match else None

        return {
            'movieList': data.get('movieList', {}).get('data', {}).get('list', []),
            'movieInfo': data.get('movieInfo', {}).get('data', {}),
            'woff_url': woff_url,
            'font_style': font_style,
        }
    except Exception as e:
        logger.error(f"  解析 dashboard 数据失败: {e}")
        return None


def fetch_movie_detail(movie_id: int) -> Optional[Dict]:
    """从电影详情页获取 JSON 数据（含日票房）"""
    url = f"https://piaofang.maoyan.com/movie/{movie_id}"
    raw = brightdata_fetch(url)
    if not raw:
        return None

    try:
        html = raw.decode('utf-8', errors='replace')
        soup = BeautifulSoup(html, 'html.parser')
        for script in soup.find_all('script', {'type': 'application/json'}):
            try:
                d = json.loads(script.string)
                if 'boxshowChartData' in d:
                    return d
            except (json.JSONDecodeError, TypeError):
                continue
        logger.warning(f"  电影 {movie_id} 未找到 JSON 数据")
        return None
    except Exception as e:
        logger.error(f"  解析电影 {movie_id} 详情页失败: {e}")
        return None


def parse_box_desc(desc: str) -> float:
    """解析票房描述文字 → 万元
    例: '6.16亿' → 61600, '1211.6万' → 1211.6, '54084.70万' → 54084.70
    """
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


def extract_daily_box_office(detail_data: Dict, pre_sale_box: float = 0.0,
                              service_fee_rate: float = 0.0) -> List[Dict]:
    """从电影详情 JSON 中提取日票房和累计票房
    - split_box: 分账票房 (原始数据)
    - total_box: 综合票房 (含服务费) = split_box * (1 + service_fee_rate)
    - 累计从 pre_sale_box 开始加总
    """
    box = detail_data.get('boxshowChartData', {}).get('chartData', {}).get('box', {})
    if not box:
        return []

    dates = box.get('date', [])
    real_vals = box.get('real', [])

    records = []
    split_total = pre_sale_box
    for i in range(len(dates)):
        daily_split = None
        if i < len(real_vals) and real_vals[i]:
            try:
                daily_split = float(real_vals[i])
                split_total += daily_split
            except (ValueError, TypeError):
                pass

        if daily_split is not None:
            daily_total = round(daily_split * (1 + service_fee_rate), 2)
            total_box = round(split_total * (1 + service_fee_rate), 2)
            records.append({
                'stat_date': dates[i],
                'daily_box': daily_total,           # 综合日票房
                'daily_split_box': daily_split,     # 分账日票房
                'total_box': total_box,             # 综合累计票房
                'total_split_box': split_total,     # 分账累计票房
            })

    return records


# ============================================================
# Supabase 存储
# ============================================================
class MaydayStorage:
    """五一档数据存储"""

    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("Supabase 连接成功")

    def upsert_movie(self, movie_id: int, movie_name: str, release_date: str) -> bool:
        """插入或更新电影信息"""
        try:
            record = {
                'movie_id': movie_id,
                'movie_name': movie_name,
                'release_date': release_date,
                'updated_at': datetime.now().isoformat(),
            }
            existing = self.client.table('mayday_movies') \
                .select('id').eq('movie_id', movie_id).execute()
            if existing.data:
                self.client.table('mayday_movies') \
                    .update(record).eq('movie_id', movie_id).execute()
                logger.info(f"  更新电影: {movie_name}")
            else:
                self.client.table('mayday_movies') \
                    .insert(record).execute()
                logger.info(f"  新增电影: {movie_name}")
            return True
        except Exception as e:
            logger.error(f"  存储电影失败: {e}")
            return False

    def upsert_daily_stats(self, movie_id: int, records: List[Dict]) -> int:
        """批量写入日票房数据（upsert）"""
        success = 0
        for rec in records:
            try:
                record = {
                    'movie_id': movie_id,
                    'stat_date': rec['stat_date'],
                    'daily_box': rec['daily_box'],
                    'daily_split_box': rec.get('daily_split_box'),
                    'total_box': rec['total_box'],
                    'total_split_box': rec.get('total_split_box'),
                    'crawl_time': datetime.now().isoformat(),
                }
                existing = self.client.table('mayday_daily_stats') \
                    .select('id') \
                    .eq('movie_id', movie_id) \
                    .eq('stat_date', rec['stat_date']) \
                    .execute()
                if existing.data:
                    rid = existing.data[0]['id']
                    self.client.table('mayday_daily_stats') \
                        .update(record).eq('id', rid).execute()
                else:
                    self.client.table('mayday_daily_stats') \
                        .insert(record).execute()
                success += 1
            except Exception as e:
                logger.error(f"  写入日票房失败 [{rec['stat_date']}]: {e}")
            time.sleep(0.15)
        return success

    def insert_dashboard_snapshot(self, movie_id: int, data: Dict) -> bool:
        """写入仪表盘快照（上座率、排片占比、综合票房等）"""
        try:
            record = {
                'movie_id': movie_id,
                'crawl_date': date.today().isoformat(),
                'avg_seat_view': data.get('avg_seat_view'),
                'avg_show_view': data.get('avg_show_view'),
                'box_rate': data.get('box_rate'),
                'show_count': data.get('show_count'),
                'show_count_rate': data.get('show_count_rate'),
                'split_box_rate': data.get('split_box_rate'),
                'sum_box_desc': data.get('sum_box_desc'),           # 综合累计票房描述
                'sum_split_box_desc': data.get('sum_split_box_desc'),  # 分账累计票房描述
                'total_box_value': data.get('total_box_value'),       # 综合累计票房(万元)
                'split_box_value': data.get('split_box_value'),       # 分账累计票房(万元)
                'service_fee_rate': data.get('service_fee_rate'),     # 服务费比例
                'pre_sale_box': data.get('pre_sale_box'),             # 预售票房(万元)
                'crawl_time': datetime.now().isoformat(),
            }
            # 每天每部电影只保留一条快照
            existing = self.client.table('mayday_dashboard') \
                .select('id') \
                .eq('movie_id', movie_id) \
                .eq('crawl_date', date.today().isoformat()) \
                .execute()
            if existing.data:
                self.client.table('mayday_dashboard') \
                    .update(record).eq('id', existing.data[0]['id']).execute()
            else:
                self.client.table('mayday_dashboard') \
                    .insert(record).execute()
            return True
        except Exception as e:
            logger.error(f"  写入仪表盘数据失败: {e}")
            return False


# ============================================================
# 主流程
# ============================================================
def main():
    logger.info("=" * 60)
    logger.info("  2026 五一档电影数据爬虫")
    logger.info(f"  档期: {MAYDAY_START} ~ {MAYDAY_END}")
    logger.info("=" * 60)

    storage = MaydayStorage()

    # ---- 第1步: 获取 dashboard 数据 ----
    dashboard = fetch_dashboard_data()
    if not dashboard:
        logger.error("❌ 无法获取 dashboard 数据，终止")
        return

    movie_list = dashboard['movieList']

    # 解码字体
    font_decoder = MaoyanFontDecoder()
    if dashboard.get('woff_url'):
        font_decoder.load_font(dashboard['woff_url'])

    # 从 dashboard 构建电影信息映射
    dashboard_movies = {}
    for m in movie_list:
        mi = m.get('movieInfo', {})
        mid = mi.get('movieId')
        if mid:
            # 解码电影名（从原始字节）
            dashboard_movies[mid] = m

    logger.info(f"Dashboard 共有 {len(movie_list)} 部电影")

    # ---- Step 2: Process each May Day movie ----
    total_daily_records = 0
    total_dashboard_records = 0

    for movie_id in MAYDAY_MOVIE_IDS:
        logger.info(f"\n{'─' * 50}")
        logger.info(f"Processing movie ID: {movie_id}")

        # 2a. Get movie detail page
        detail = fetch_movie_detail(movie_id)
        if not detail:
            logger.warning(f"  Skip movie {movie_id} (no detail data)")
            continue

        movie_name = detail.get('movieName', f'Movie_{movie_id}')
        release_date = detail.get('releaseDate', '')
        logger.info(f"  {movie_name} ({release_date})")

        # 2b. Get total/split box from dashboard (plain text, not font-encoded!)
        dm = dashboard_movies.get(movie_id)
        sum_total_desc = dm.get('sumBoxDesc', '') if dm else ''
        sum_split_desc = dm.get('sumSplitBoxDesc', '') if dm else ''

        total_box_dashboard = parse_box_desc(sum_total_desc)
        split_box_dashboard = parse_box_desc(sum_split_desc)

        # 2c. Calculate raw split cumulative (no pre-sale, no service fee)
        raw_records = extract_daily_box_office(detail, pre_sale_box=0, service_fee_rate=0)
        raw_split_cumulative = raw_records[-1]['total_box'] if raw_records else 0

        # Pre-sale box = dashboard split - raw split cumulative
        pre_sale_box = max(0, round(split_box_dashboard - raw_split_cumulative, 2))

        # Service fee rate = (total - split) / split
        service_fee_rate = 0.0
        if split_box_dashboard > 0:
            service_fee_rate = round((total_box_dashboard - split_box_dashboard) / split_box_dashboard, 6)

        logger.info(f"  Dashboard total box: {sum_total_desc} ({total_box_dashboard:.2f}w)")
        logger.info(f"  Dashboard split box: {sum_split_desc} ({split_box_dashboard:.2f}w)")
        logger.info(f"  Pre-sale box: {pre_sale_box:.2f}w")
        logger.info(f"  Service fee rate: {service_fee_rate*100:.2f}%")

        # 2d. Calculate daily box with pre-sale + service fee
        daily_records = extract_daily_box_office(detail, pre_sale_box, service_fee_rate)
        logger.info(f"  Got {len(daily_records)} days of box office data")

        # 2e. Save movie info
        storage.upsert_movie(movie_id, movie_name, release_date)

        if daily_records:
            mayday_records = [r for r in daily_records
                            if MAYDAY_START <= r['stat_date'] <= MAYDAY_END]
            logger.info(f"  May Day period ({MAYDAY_START}~{MAYDAY_END}): {len(mayday_records)} days")

            for r in mayday_records:
                logger.info(f"    {r['stat_date']}: daily={r['daily_box']:.2f}w, total={r['total_box']/10000:.2f}yi")

            n = storage.upsert_daily_stats(movie_id, daily_records)
            total_daily_records += n

        # 2f. Save dashboard snapshot
        if dm:
            dashboard_record = {
                'avg_seat_view': dm.get('avgSeatView', ''),
                'avg_show_view': dm.get('avgShowView', ''),
                'box_rate': dm.get('boxRate', ''),
                'show_count': dm.get('showCount', 0),
                'show_count_rate': dm.get('showCountRate', ''),
                'split_box_rate': dm.get('splitBoxRate', ''),
                'sum_box_desc': sum_total_desc,
                'sum_split_box_desc': sum_split_desc,
                'total_box_value': total_box_dashboard,
                'split_box_value': split_box_dashboard,
                'service_fee_rate': service_fee_rate,
                'pre_sale_box': pre_sale_box,
            }
            storage.insert_dashboard_snapshot(movie_id, dashboard_record)
            total_dashboard_records += 1

            logger.info(f"  Seat: {dm.get('avgSeatView', 'N/A')}")
            logger.info(f"  Show rate: {dm.get('showCountRate', 'N/A')}")
            logger.info(f"  Box rate: {dm.get('boxRate', 'N/A')}")
            logger.info(f"  Total: {sum_total_desc} | Split: {sum_split_desc}")

        time.sleep(REQUEST_DELAY)

    # ---- 第3步: 汇总 ----
    logger.info(f"\n{'=' * 60}")
    logger.info(f"[OK] 爬取完成")
    logger.info(f"  日票房记录: {total_daily_records} 条")
    logger.info(f"  仪表盘快照: {total_dashboard_records} 条")
    logger.info(f"  五一档电影: {len(MAYDAY_MOVIE_IDS)} 部")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n用户中断")
        sys.exit(130)
    except Exception as e:
        logger.error(f"异常: {e}", exc_info=True)
        sys.exit(1)
