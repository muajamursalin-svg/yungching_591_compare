from __future__ import annotations
import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
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

        # 1.5 僅在有成功獲取到物件時清理既有快照，避免抓取異常時誤刪
        if houses:
            self.db.execute("DELETE FROM t591_houses WHERE shop_id = ?;", [shop_id])
        else:
            print(f"[Warn] 591 本次未抓取到物件，保留現有 Turso 快照不作刪除。")
            return

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

    @staticmethod
    def to_standard_houses(houses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        將 591 原始案件資料轉換為跨平台通用 StandardHouse 規格
        """
        standard_list = []
        for h in houses:
            hid = str(h.get("id"))
            p_val = T591ShopCrawler.parse_price(h.get("price", ""))
            fl = int(h.get("floor")) if str(h.get("floor", "")).isdigit() else 0
            allfl = int(h.get("allfloor")) if str(h.get("allfloor", "")).isdigit() else 0
            room = int(h.get("room")) if str(h.get("room", "")).isdigit() else 0
            raw_age = str(h.get("houseage", "")).strip()
            age_val = float(raw_age) if raw_age.replace(".", "").isdigit() else 0.0

            # 若為透天或整棟，樓層標記為 99
            kind = h.get("kind_name") or ""
            shape = int(h.get("shape") or 0)
            if shape in [3, 4] or any(k in kind for k in ["透天", "別墅", "整棟"]):
                if fl in [0, 1, 99]:
                    fl = 99

            # 判斷是否為土地
            is_land = (shape == 0) or any(k in (kind + (h.get("fulladdress") or "") + (h.get("address") or "")) for k in ["土地", "農地", "建地", "林地", "山坡地", "工商業地"])
            g_area = float(h.get("groundarea") or 0.0)
            area_val = g_area if (is_land and g_area > 0) else float(h.get("area") or 0.0)
            case_type_val = "土地" if is_land else (kind or ("透天/別墅" if shape in [3, 4] else "大樓/華廈"))

            std_item = {
                "platform": "591",
                "house_id": hid,
                "title": h.get("fulladdress") or h.get("address") or "",
                "url": f"https://sale.591.com.tw/sale-detail-{hid}.html",
                "county": (h.get("regionname") or "").strip(),
                "district": (h.get("sectionname") or "").strip(),
                "street": (h.get("street_name") or "").strip(),
                "community_name": re.sub(r'<[^>]+>', '', h.get("cases_name") or "").strip(),
                "price": p_val,
                "reg_area": area_val,
                "land_area": g_area if g_area > 0 else area_val,
                "is_land": is_land,
                "main_area": float(h.get("mainarea") or 0.0) if h.get("mainarea") else None,
                "floor": fl,
                "all_floor": allfl,
                "house_age": age_val,
                "room": room,
                "case_type": case_type_val,
                "cover": h.get("cover") or "",
                "agent_name": h.get("linkman") or ""
            }
            standard_list.append(std_item)
        return standard_list

    def crawl_and_get_standard_houses(self, shop_input: str) -> Tuple[str, str, List[Dict[str, Any]]]:
        """
        爬取 591 店鋪，持久化至 Turso，並回傳 (shop_id, full_shop_url, standard_houses)
        """
        shop_id = self.extract_shop_id(shop_input)
        full_shop_url = f"https://www.591.com.tw/broker{shop_id}"
        print(f"\n================ 開始抓取 591 店鋪 {shop_id} ================")
        houses = self.fetch_all_houses(shop_id)
        print(f"共抓取 {len(houses)} 筆 591 在售庫存，正在儲存至 Turso...")
        self.save_to_turso(shop_id, houses)
        print(f"✅ 591 店鋪 {shop_id} 快照儲存完成！")
        std_houses = self.to_standard_houses(houses)
        return shop_id, full_shop_url, std_houses

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
