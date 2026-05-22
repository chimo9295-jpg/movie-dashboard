-- ============================================================
-- 2026 五一档电影数据 - Supabase 建表 SQL
-- 在 Supabase SQL Editor 中执行
-- ============================================================

-- 1. 电影信息表
CREATE TABLE IF NOT EXISTS mayday_movies (
    id          BIGSERIAL PRIMARY KEY,
    movie_id    BIGINT UNIQUE NOT NULL,        -- 猫眼电影ID
    movie_name  TEXT NOT NULL,                 -- 电影名称
    release_date TEXT,                         -- 上映日期
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mayday_movies_movie_id ON mayday_movies(movie_id);

-- 2. 日票房数据表
CREATE TABLE IF NOT EXISTS mayday_daily_stats (
    id          BIGSERIAL PRIMARY KEY,
    movie_id    BIGINT NOT NULL,               -- 猫眼电影ID
    stat_date   DATE NOT NULL,                 -- 统计日期
    daily_box   DOUBLE PRECISION,              -- 日票房（万元）
    total_box   DOUBLE PRECISION,              -- 累计票房（万元）
    crawl_time  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(movie_id, stat_date)
);

CREATE INDEX IF NOT EXISTS idx_mayday_daily_movie ON mayday_daily_stats(movie_id);
CREATE INDEX IF NOT EXISTS idx_mayday_daily_date  ON mayday_daily_stats(stat_date);

-- 3. 实时仪表盘快照表（上座率、排片占比等）
CREATE TABLE IF NOT EXISTS mayday_dashboard (
    id              BIGSERIAL PRIMARY KEY,
    movie_id        BIGINT NOT NULL,
    crawl_date      DATE NOT NULL,             -- 爬取日期
    avg_seat_view   TEXT,                      -- 平均上座率 (如 "6.8%")
    avg_show_view   TEXT,                      -- 场均人次
    box_rate        TEXT,                      -- 票房占比
    show_count      BIGINT,                    -- 排片场次
    show_count_rate TEXT,                      -- 排片占比
    split_box_rate  TEXT,                      -- 分账票房占比
    sum_box_desc    TEXT,                      -- 累计票房描述（解码后）
    crawl_time      TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(movie_id, crawl_date)
);

CREATE INDEX IF NOT EXISTS idx_mayday_dashboard_movie ON mayday_dashboard(movie_id);
CREATE INDEX IF NOT EXISTS idx_mayday_dashboard_date  ON mayday_dashboard(crawl_date);

-- 4. 城市分布数据表（预留，需猫眼专业版 API）
CREATE TABLE IF NOT EXISTS mayday_city_split (
    id          BIGSERIAL PRIMARY KEY,
    movie_id    BIGINT NOT NULL,
    stat_date   DATE NOT NULL,
    city_name   TEXT NOT NULL,                 -- 城市名称
    box_ratio   DOUBLE PRECISION,              -- 票房占比 (%)
    box_amount  DOUBLE PRECISION,              -- 票房金额（万元）
    seat_ratio  DOUBLE PRECISION,              -- 上座率 (%)
    crawl_time  TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(movie_id, stat_date, city_name)
);

CREATE INDEX IF NOT EXISTS idx_mayday_city_movie ON mayday_city_split(movie_id);
CREATE INDEX IF NOT EXISTS idx_mayday_city_date  ON mayday_city_split(stat_date);

-- ============================================================
-- 示例查询
-- ============================================================

-- 查看五一档电影列表
-- SELECT * FROM mayday_movies ORDER BY release_date;

-- 查看某部电影的日票房趋势
-- SELECT stat_date, daily_box, total_box
-- FROM mayday_daily_stats
-- WHERE movie_id = 1516982
-- ORDER BY stat_date;

-- 查看五一档期间所有电影的累计票房对比
-- SELECT m.movie_name, d.total_box
-- FROM mayday_daily_stats d
-- JOIN mayday_movies m ON m.movie_id = d.movie_id
-- WHERE d.stat_date = '2026-05-05'
-- ORDER BY d.total_box DESC;

-- 查看最新的上座率数据
-- SELECT m.movie_name, d.avg_seat_view, d.box_rate, d.show_count_rate
-- FROM mayday_dashboard d
-- JOIN mayday_movies m ON m.movie_id = d.movie_id
-- WHERE d.crawl_date = CURRENT_DATE;
