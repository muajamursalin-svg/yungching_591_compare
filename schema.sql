-- 永慶分店清單表
CREATE TABLE IF NOT EXISTS branches (
    branch_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT,
    company_name TEXT,
    county TEXT,
    district TEXT,
    address TEXT,
    phone TEXT,
    enabled INTEGER DEFAULT 1,
    last_synced_at TEXT
);

-- 永慶分店在庫物件總表 (多店集中庫存)
CREATE TABLE IF NOT EXISTS yungching_houses (
    case_sid INTEGER PRIMARY KEY,
    branch_id TEXT NOT NULL,
    case_key TEXT,
    show_case_no TEXT,
    case_name TEXT NOT NULL,
    case_type TEXT,
    purpose TEXT,
    county TEXT,
    district TEXT,
    address TEXT,
    community_id INTEGER,
    community_name TEXT,
    price REAL,             -- 總價(萬)
    last_price REAL,        -- 降價前總價(萬)
    reg_area REAL,          -- 總登記坪數
    main_area REAL,         -- 主建物坪數
    land_area REAL,         -- 土地坪數
    from_floor INTEGER,     -- 起始樓層
    to_floor INTEGER,       -- 結束樓層
    up_floor INTEGER,       -- 建物總樓層
    room REAL,              -- 房間數
    living_room REAL,       -- 廳數
    bath_room REAL,         -- 衛數
    parking TEXT,           -- 車位說明
    build_age REAL,         -- 屋齡
    case_feature TEXT,      -- 特色描述
    cover TEXT,             -- 封面圖 URL
    is_exclusive INTEGER,   -- 是否專任 (1/0)
    is_discount INTEGER,    -- 是否降價 (1/0)
    tag TEXT,               -- 標籤 JSON
    updated_at TEXT,
    FOREIGN KEY(branch_id) REFERENCES branches(branch_id)
);

-- 索引優化：加速以縣市、行政區、社區、坪數進行比對查詢
CREATE INDEX IF NOT EXISTS idx_yc_search ON yungching_houses (county, district, community_name, reg_area);
CREATE INDEX IF NOT EXISTS idx_yc_branch ON yungching_houses (branch_id);

-- 591 店鋪表
CREATE TABLE IF NOT EXISTS t591_shops (
    shop_id TEXT PRIMARY KEY,
    agent_name TEXT,
    shop_title TEXT,
    sale_count INTEGER,
    last_checked_at TEXT
);

-- 591 房屋物件快照表
CREATE TABLE IF NOT EXISTS t591_houses (
    house_id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    title TEXT NOT NULL,
    price REAL,             -- 總價(萬，已轉換為數值)
    price_str TEXT,         -- 原始總價文字 (例如 '2,480萬元')
    per_area_price REAL,    -- 單價(萬/坪)
    reg_area REAL,          -- 總坪數
    main_area REAL,         -- 主建物坪數
    ground_area REAL,       -- 地坪
    floor INTEGER,          -- 樓層 (99為整棟)
    all_floor INTEGER,      -- 總樓層
    room INTEGER,           -- 房數
    layout TEXT,            -- 格局 (例如 '5房3廳6衛')
    county TEXT,            -- 縣市 (例如 '台中市')
    district TEXT,          -- 行政區 (例如 '太平區')
    street TEXT,            -- 路街名 (例如 '大源五街')
    community_name TEXT,    -- 社區名稱
    kind_name TEXT,         -- 種類名稱 (住宅、店面...)
    shape INTEGER,          -- 建築形態代碼 (0:地, 1:公, 2:大樓, 3:透, 4:別)
    house_age REAL,         -- 屋齡 (年)
    cover TEXT,             -- 圖片 URL
    updated_at TEXT,
    FOREIGN KEY(shop_id) REFERENCES t591_shops(shop_id)
);

CREATE INDEX IF NOT EXISTS idx_591_shop ON t591_houses (shop_id);

-- 比對報告主表
CREATE TABLE IF NOT EXISTS compare_reports (
    report_id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL,
    shop_url TEXT,              -- 591 店鋪完整網址
    user_id TEXT,               -- LINE 使用者 ID (選填)
    total_591_count INTEGER,
    matched_count INTEGER,
    price_diff_count INTEGER,
    not_found_count INTEGER,
    spec_diff_count INTEGER,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_compare_cache ON compare_reports (user_id, shop_id, created_at);

-- 比對報告明細表 (LIFF 前端主要查詢來源)
CREATE TABLE IF NOT EXISTS compare_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    t591_id TEXT NOT NULL,
    t591_title TEXT,
    t591_price REAL,
    t591_area REAL,
    t591_floor TEXT,
    t591_community TEXT,
    t591_cover TEXT,
    t591_url TEXT,
    matched_case_sid INTEGER,
    matched_branch_id TEXT,
    matched_branch_name TEXT,
    matched_price REAL,
    matched_url TEXT,
    status TEXT NOT NULL,       -- 'NORMAL', 'PRICE_MISMATCH', 'NOT_FOUND', 'SPEC_MISMATCH'
    diff_reason TEXT,           -- 詳細原因說明
    confidence_score REAL,      -- 匹配信心度 (0 ~ 100)
    created_at TEXT,
    FOREIGN KEY(report_id) REFERENCES compare_reports(report_id)
);

CREATE INDEX IF NOT EXISTS idx_compare_items_report ON compare_items (report_id, status);
