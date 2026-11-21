# -*- coding: utf-8 -*-
from __future__ import annotations
"""
樂屋網 (Rakuya) - 房仲店鋪免 DOM 高效串流爬取模組
基於 curl_cffi TLS 指紋模擬與正則串流萃取，徹底免除無頭瀏覽器與 DOM 樹建構開銷。
"""

import re
import time
import json
from curl_cffi import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from turso_client import TursoClient

TAIWAN_TZ = timezone(timedelta(hours=8))

def get_tw_now_str() -> str:
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

def parse_taiwan_address(raw_addr: str) -> Tuple[str, str, str]:
    if not raw_addr:
        return "", "", ""
    m = re.match(r'^(.*?([縣市]))(.*?([區鄉鎮市]))(.*)$', raw_addr)
    if m:
        return m.group(1).strip(), m.group(3).strip(), m.group(5).strip()
    county = raw_addr[:3] if len(raw_addr) >= 3 else ""
    district = raw_addr[3:6] if len(raw_addr) >= 6 else ""
    return county, district, raw_addr[6:]

class RakuyaShopCrawler:
    BASE_URL = "https://vip.rakuya.com.tw"

    def __init__(self, db_client: Optional[TursoClient] = None, timeout: int = 20):
        self.db = db_client or TursoClient()
        self.timeout = timeout

    @staticmethod
    def extract_shop_id(input_str: str) -> str:
        """
        從使用者輸入的樂屋網店鋪網址提取手機/帳號
        支援：
        1. https://vip.rakuya.com.tw/0937234338/
        2. https://vip.rakuya.com.tw/0937234338/sell
        3. 純手機號碼: 0937234338
        """
        s = input_str.strip()
        m_vip = re.search(r'vip\.rakuya\.com\.tw/([0-9a-zA-Z_-]+)', s)
        if m_vip:
            return m_vip.group(1)

        m_phone = re.search(r'(09\d{8})', s)
        if m_phone:
            return m_phone.group(1)

        m_num = re.search(r'(\d{6,12})', s)
        if m_num:
            return m_num.group(1)

        raise ValueError(f"無法解析樂屋網店鋪代號或網址: {input_str}")

    def fetch_page_html(self, shop_id: str, page: int = 1, sort: str = "21") -> str:
        # 使用 sort=21 (總價由高至低) 作為穩定排序，徹底避免 sort=11 (更新時間) 在分頁爬取過程中因時間戳記變動導致的「分頁排序漂移」
        url = f"{self.BASE_URL}/{shop_id}/sell?page={page}&sort={sort}#total"
        impersonate_profiles = ["chrome124", "chrome120", "safari15_5"]
        last_err = None
        for attempt in range(len(impersonate_profiles)):
            prof = impersonate_profiles[attempt]
            try:
                res = requests.get(url, impersonate=prof, timeout=self.timeout)
                if res.status_code == 200 and len(res.text) > 1000:
                    return res.text
                else:
                    last_err = RuntimeError(f"樂屋網請求失敗 (HTTP {res.status_code}): {url}")
            except Exception as e:
                last_err = e
            time.sleep(1.0)
        raise last_err or RuntimeError(f"樂屋網請求重試失敗: {url}")

    def extract_total_pages(self, html: str) -> int:
        m = re.search(r'class="pages">第\s*\d+\s*/\s*(\d+)\s*頁', html)
        return int(m.group(1)) if m else 1

    def extract_shop_meta(self, html: str, shop_id: str) -> Dict[str, str]:
        """從 HTML 標頭或側邊欄萃取房仲姓名與店名"""
        agent_name = ""
        company_name = ""
        shop_title = ""

        # 嘗試從 title 萃取
        m_title = re.search(r'<title>([^<]+)</title>', html)
        if m_title:
            raw_title = m_title.group(1)
            # 例如: 永慶不動產安和福科店【Dior小蔡】，專營台中市梧棲區房屋買賣
            m_agent = re.search(r'【(.*?)】', raw_title)
            if m_agent:
                agent_name = m_agent.group(1)
            shop_title = raw_title.split('，')[0].strip()

        # 側邊欄加盟店資訊
        m_comp = re.search(r'class="store-name"[^>]*>([^<]+)</span>', html)
        if m_comp:
            company_name = m_comp.group(1).strip()

        return {
            "shop_id": shop_id,
            "agent_name": agent_name or shop_id,
            "shop_title": shop_title or f"樂屋網店鋪 {shop_id}",
            "company_name": company_name
        }

    def parse_houses_from_html(self, html: str, shop_id: str, seen_ehids: set) -> List[Dict[str, Any]]:
        houses = []
        # 精準鎖定全部物件區塊 (自 <A name="total"> 至 class="pagination")，避開上方店長推薦/人氣輪播干擾，並以 |$ 確保最後一筆卡片完整截取
        if 'name="total"' in html:
            tot_html = html.split('name="total"')[-1].split('class="pagination"')[0]
        elif 'class="pagination"' in html:
            tot_html = html.split('class="pagination"')[0]
        else:
            tot_html = html

        items = re.findall(
            r'<div class="obj-item clearfix">([\s\S]*?)(?=<div class="obj-item clearfix">|$)',
            tot_html
        )

        for raw_item in items:
            ehid_m = re.search(r'data-ehid="([^"]+)"', raw_item) or re.search(r'ehid=([a-zA-Z0-9]+)', raw_item)
            if not ehid_m:
                continue
            ehid = ehid_m.group(1)
            if ehid in seen_ehids:
                continue
            seen_ehids.add(ehid)

            title_m = re.search(r'<h6><a[^>]*>([^<]+)</a></h6>', raw_item)
            title = title_m.group(1).strip() if title_m else ""

            cover_m = re.search(r'style="background-image:url\(\'([^\']+)\'\)"', raw_item)
            cover = cover_m.group(1) if cover_m else ""

            addr_m = re.search(r'class="obj-address">([^<]+)</p>', raw_item)
            raw_addr = addr_m.group(1).strip() if addr_m else ""
            county, district, street = parse_taiwan_address(raw_addr)

            price_m = re.search(r'class="obj-price">\s*<span>\s*([\d\.,]+)萬', raw_item)
            price = float(price_m.group(1).replace(",", "")) if price_m else 0.0

            unit_price_m = re.search(r'單價<b>([\d\.,]+)萬/坪</b>', raw_item)
            unit_price = float(unit_price_m.group(1).replace(",", "")) if unit_price_m else 0.0

            layout_m = re.search(r'<span>\s*(\d+房\d+廳\d+衛)\s*</span>', raw_item)
            layout = layout_m.group(1) if layout_m else ""
            room_m = re.search(r'(\d+)房', layout)
            room = int(room_m.group(1)) if room_m else 0

            area_m = re.search(r'<span>\s*([\d\.]+)坪\s*</span>', raw_item)
            reg_area = float(area_m.group(1)) if area_m else 0.0

            # 支援單一樓層 (16/27樓)、整棟 (整棟/4樓)、樓中樓/多樓層區間 (16~17/27樓 或 16-17/27樓 或 16、17/27樓 或 B1~1/5樓)
            floor_m = re.search(r'<span>\s*([B\d]+(?:\s*[-~～至到、]\s*[B\d]+)?|整棟)樓?\s*/\s*(\d+)樓?\s*</span>', raw_item)
            if floor_m:
                f_raw = floor_m.group(1).strip()
                all_floor = int(floor_m.group(2))
                if f_raw == "整棟":
                    floor = 99
                    to_floor = 99
                elif any(sep in f_raw for sep in ["~", "-", "～", "至", "到", "、"]):
                    parts = re.split(r'[-~～至到、]', f_raw)
                    nums = []
                    for p in parts:
                        nm = re.search(r'\d+', p)
                        if nm:
                            v = int(nm.group(0))
                            if 'B' in p.upper():
                                v = -v
                            nums.append(v)
                    floor = min(nums) if nums else 0
                    to_floor = max(nums) if nums else 0
                else:
                    num_m = re.search(r'\d+', f_raw)
                    val = int(num_m.group(0)) if num_m else 0
                    if 'B' in f_raw.upper():
                        val = -val
                    floor = val
                    to_floor = val
            else:
                floor, to_floor, all_floor = 0, 0, 0

            age_m = re.search(r'屋齡<b>([\d\.]+)年</b>', raw_item)
            house_age = float(age_m.group(1)) if age_m else 0.0

            type_m = re.search(r'<li class="clearfix">\s*<span>([^<]+)</span>', raw_item)
            case_type = type_m.group(1).strip() if type_m else ""

            # 社區名稱嘗試從標題中的分隔符號萃取 (例如: ｜半半居 或 ｜麗晨朵也)
            community_name = ""
            for sep in ["｜", "|", "／", "/"]:
                if sep in title:
                    tokens = [t.strip() for t in title.split(sep) if t.strip()]
                    if tokens:
                        # 通常社區名位於末尾
                        candidate = tokens[-1]
                        if not any(k in candidate for k in ["車位", "平車", "大三房", "大2房", "視野", "採光", "精裝"]):
                            community_name = candidate
                    break

            # 建物型態若為透天/別墅，且樓層為 0 或 1，相容標為 99 (整棟)
            if any(t in case_type for t in ["透天", "別墅", "整棟"]):
                if floor in [0, 1]:
                    floor = 99

            # 判斷是否為土地 (依樂屋網結構化 case_type 標籤判斷，絕不透過 title 判斷)
            is_land = any(k == case_type for k in ["土地", "農地", "建地", "林地", "山坡地", "工商業地"])
            if is_land:
                case_type = "土地"

            houses.append({
                "platform": "rakuya",
                "house_id": ehid,
                "shop_id": shop_id,
                "title": title,
                "url": f"https://www.rakuya.com.tw/sell_item/info?ehid={ehid}&from=store",
                "county": county,
                "district": district,
                "street": street,
                "community_name": community_name,
                "price": price,
                "unit_price": unit_price,
                "reg_area": reg_area,
                "land_area": reg_area,
                "is_land": is_land,
                "main_area": None,
                "floor": floor,
                "to_floor": to_floor,
                "all_floor": all_floor,
                "layout": layout,
                "room": room,
                "case_type": case_type,
                "house_age": house_age,
                "cover": cover
            })

        return houses

    def fetch_all_houses(self, shop_id: str) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
        print(f"[*] [樂屋網] 開始抓取店鋪 {shop_id} 物件清單...")
        html1 = self.fetch_page_html(shop_id, 1)
        meta = self.extract_shop_meta(html1, shop_id)
        total_pages = self.extract_total_pages(html1)
        print(f"[+] 店鋪: {meta.get('shop_title')}，共有 {total_pages} 頁")

        seen_ehids = set()
        all_houses = self.parse_houses_from_html(html1, shop_id, seen_ehids)
        print(f"  第 1/{total_pages} 頁，累計 {len(all_houses)} 筆")

        for p in range(2, total_pages + 1):
            time.sleep(0.3)
            try:
                html = self.fetch_page_html(shop_id, p)
                items = self.parse_houses_from_html(html, shop_id, seen_ehids)
                all_houses.extend(items)
                print(f"  第 {p}/{total_pages} 頁，累計 {len(all_houses)} 筆")
            except Exception as e:
                print(f"[!] 抓取第 {p} 頁失敗: {e}")
                break

        print(f"[OK] 樂屋網店鋪 {shop_id} 抓取完成！共收集 {len(all_houses)} 筆在庫物件")
        return meta, all_houses

    def save_to_turso(self, meta: Dict[str, str], houses: List[Dict[str, Any]]):
        """將樂屋網店鋪與物件快照保存至 Turso"""
        now_str = get_tw_now_str()
        shop_id = meta["shop_id"]

        # 1. 儲存店鋪資訊
        self.db.execute(
            """
            INSERT INTO trakuya_shops (shop_id, agent_name, shop_title, company_name, sale_count, last_checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(shop_id) DO UPDATE SET
                agent_name=excluded.agent_name,
                shop_title=excluded.shop_title,
                company_name=excluded.company_name,
                sale_count=excluded.sale_count,
                last_checked_at=excluded.last_checked_at;
            """,
            [shop_id, meta.get("agent_name"), meta.get("shop_title"), meta.get("company_name"), len(houses), now_str]
        )

        # 1.5 僅在有成功獲取到物件時清理既有快照，徹底避免異常時誤刪
        if houses:
            self.db.execute("DELETE FROM trakuya_houses WHERE shop_id = ?;", [shop_id])
        else:
            print(f"[Warn] 樂屋網 本次未抓取到物件，保留現有 Turso 快照不作刪除。")
            return

        # 2. 批量儲存物件快照
        upsert_sql = """
            INSERT INTO trakuya_houses (
                house_id, shop_id, title, price, unit_price, reg_area,
                floor, all_floor, layout, room, county, district, street,
                community_name, case_type, house_age, cover, url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(house_id) DO UPDATE SET
                shop_id=excluded.shop_id,
                title=excluded.title,
                price=excluded.price,
                unit_price=excluded.unit_price,
                reg_area=excluded.reg_area,
                floor=excluded.floor,
                all_floor=excluded.all_floor,
                layout=excluded.layout,
                room=excluded.room,
                county=excluded.county,
                district=excluded.district,
                street=excluded.street,
                community_name=excluded.community_name,
                case_type=excluded.case_type,
                house_age=excluded.house_age,
                cover=excluded.cover,
                url=excluded.url,
                updated_at=excluded.updated_at;
        """

        batch_stmts = []
        for h in houses:
            params = [
                h["house_id"],
                shop_id,
                h["title"],
                h["price"],
                h.get("unit_price") or 0.0,
                h["reg_area"],
                h["floor"],
                h["all_floor"],
                h.get("layout") or "",
                h["room"],
                h["county"],
                h["district"],
                h["street"],
                h.get("community_name") or "",
                h.get("case_type") or "",
                h["house_age"],
                h.get("cover") or "",
                h["url"],
                now_str
            ]
            batch_stmts.append((upsert_sql, params))
            if len(batch_stmts) >= 50:
                self.db.batch(batch_stmts)
                batch_stmts = []

        if batch_stmts:
            self.db.batch(batch_stmts)

    def crawl_and_get_standard_houses(self, shop_input: str) -> Tuple[str, str, List[Dict[str, Any]]]:
        """
        爬取樂屋網店鋪，持久化至 Turso，並回傳 (shop_id, full_shop_url, standard_houses)
        """
        shop_id = self.extract_shop_id(shop_input)
        full_shop_url = f"https://vip.rakuya.com.tw/{shop_id}/sell"
        print(f"\n================ 開始抓取樂屋網店鋪 {shop_id} ================")
        meta, houses = self.fetch_all_houses(shop_id)
        print(f"共抓取 {len(houses)} 筆樂屋網在售庫存，正在儲存至 Turso...")
        self.save_to_turso(meta, houses)
        print(f"✅ 樂屋網店鋪 {shop_id} 快照儲存完成！")
        return shop_id, full_shop_url, houses

if __name__ == "__main__":
    crawler = RakuyaShopCrawler()
    sid, url, std_list = crawler.crawl_and_get_standard_houses("https://vip.rakuya.com.tw/0937234338/")
    print(f"\n完成！取得 {len(std_list)} 筆標準物件，首筆：")
    if std_list:
        print(json.dumps(std_list[0], ensure_ascii=False, indent=2))
