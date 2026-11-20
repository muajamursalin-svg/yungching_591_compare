import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from turso_client import TursoClient

TAIWAN_TZ = timezone(timedelta(hours=8))

def get_tw_now_str() -> str:
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

class T591ShopCrawler:
    BASE_URL = "https://bff-house.591.com.tw/v2/web/shop/house/list"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://www.591.com.tw/",
        "Accept": "application/json, text/plain, */*",
    }

    def __init__(self, db_client: Optional[TursoClient] = None):
        self.db = db_client or TursoClient()

    @staticmethod
    def extract_shop_id(input_str: str) -> str:
        """從使用者輸入的網址或字串提取 591 店鋪 ID (如 broker12017 -> 12017)"""
        match = re.search(r'broker(\d+)', input_str)
        if match:
            return match.group(1)
        digit_match = re.search(r'(\d+)', input_str)
        if digit_match:
            return digit_match.group(1)
        raise ValueError(f"無法解析 591 店鋪 ID: {input_str}")

    @staticmethod
    def parse_price(price_str: str) -> float:
        """將 '2,480萬元' 或 '2500萬' 轉換為浮點數 2480.0"""
        if not price_str:
            return 0.0
        cleaned = price_str.replace(",", "").replace("萬元", "").replace("萬", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    def fetch_all_houses(self, shop_id: str) -> List[Dict[str, Any]]:
        all_houses = []
        first_row = 0
        agent_name = ""
        
        while True:
            params = {
                "module": "shop",
                "action": "house",
                "respType": "json",
                "shop_id": shop_id,
                "type": "2", # 2: 出售
                "firstRow": first_row,
                "device": "pc",
                "timestamp": int(time.time() * 1000)
            }
            res = requests.get(self.BASE_URL, params=params, headers=self.HEADERS, timeout=15)
            res.raise_for_status()
            data = res.json()
            items = data.get("data", [])
            
            if not items:
                break
                
            all_houses.extend(items)
            if not agent_name and items:
                agent_name = items[0].get("linkman", "")

            print(f"  [591 店鋪 {shop_id}] 已抓取 {len(all_houses)} 筆物件...")
            first_row += len(items)
            time.sleep(0.3)

        return all_houses

    def save_to_turso(self, shop_id: str, houses: List[Dict[str, Any]]):
        now_str = get_tw_now_str()
        agent_name = houses[0].get("linkman", "") if houses else ""
        shop_title = houses[0].get("nick_name", "") if houses else ""

        # 更新店鋪資訊
        self.db.execute(
            """
            INSERT INTO t591_shops (shop_id, agent_name, shop_title, sale_count, last_checked_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(shop_id) DO UPDATE SET
                agent_name=excluded.agent_name,
                shop_title=excluded.shop_title,
                sale_count=excluded.sale_count,
                last_checked_at=excluded.last_checked_at;
            """,
            [shop_id, agent_name, shop_title, len(houses), now_str]
        )

        # 批次更新物件快照
        batch_stmts = []
        upsert_sql = """
            INSERT INTO t591_houses (
                house_id, shop_id, title, price, price_str, per_area_price,
                reg_area, main_area, ground_area, floor, all_floor, room, layout,
                county, district, street, community_name, kind_name, shape, house_age, cover, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(house_id) DO UPDATE SET
                shop_id=excluded.shop_id,
                title=excluded.title,
                price=excluded.price,
                price_str=excluded.price_str,
                per_area_price=excluded.per_area_price,
                reg_area=excluded.reg_area,
                main_area=excluded.main_area,
                ground_area=excluded.ground_area,
                floor=excluded.floor,
                all_floor=excluded.all_floor,
                room=excluded.room,
                layout=excluded.layout,
                county=excluded.county,
                district=excluded.district,
                street=excluded.street,
                community_name=excluded.community_name,
                kind_name=excluded.kind_name,
                shape=excluded.shape,
                house_age=excluded.house_age,
                cover=excluded.cover,
                updated_at=excluded.updated_at;
        """

        for h in houses:
            hid = str(h.get("id"))
            p_val = self.parse_price(h.get("price", ""))
            
            per_area_str = h.get("perarea_str", "")
            per_val = 0.0
            per_m = re.search(r'([\d.]+)', per_area_str)
            if per_m:
                try:
                    per_val = float(per_m.group(1))
                except ValueError:
                    pass

            fl = int(h.get("floor")) if str(h.get("floor", "")).isdigit() else 0
            allfl = int(h.get("allfloor")) if str(h.get("allfloor", "")).isdigit() else 0
            room = int(h.get("room")) if str(h.get("room", "")).isdigit() else 0
            shape = int(h.get("shape")) if str(h.get("shape", "")).isdigit() else 0

            # 屋齡
            raw_age = str(h.get("houseage", "")).strip()
            age_val = float(raw_age) if raw_age.replace(".", "").isdigit() else 0.0

            params = [
                hid,
                shop_id,
                h.get("fulladdress") or h.get("address") or "",
                p_val,
                h.get("price") or "",
                per_val,
                float(h.get("area") or 0.0),
                float(h.get("mainarea") or 0.0),
                float(h.get("groundarea") or 0.0),
                fl,
                allfl,
                room,
                h.get("layout") or "",
                h.get("regionname") or "",
                h.get("sectionname") or "",
                h.get("street_name") or "",
                re.sub(r'<[^>]+>', '', h.get("cases_name") or "").strip(),
                h.get("kind_name") or "",
                shape,
                age_val,
                h.get("cover") or "",
                now_str
            ]
            batch_stmts.append((upsert_sql, params))
            if len(batch_stmts) >= 50:
                self.db.batch(batch_stmts)
                batch_stmts = []

        if batch_stmts:
            self.db.batch(batch_stmts)

    def crawl_and_save(self, shop_input: str) -> str:
        shop_id = self.extract_shop_id(shop_input)
        print(f"\n================ 開始抓取 591 店鋪 {shop_id} ================")
        houses = self.fetch_all_houses(shop_id)
        print(f"共抓取 {len(houses)} 筆 591 在售庫存，正在儲存至 Turso...")
        self.save_to_turso(shop_id, houses)
        print(f"✅ 591 店鋪 {shop_id} 快照儲存完成！")
        return shop_id

if __name__ == "__main__":
    crawler = T591ShopCrawler()
    crawler.crawl_and_save("https://www.591.com.tw/broker12017")
