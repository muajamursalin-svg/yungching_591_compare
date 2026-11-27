# -*- coding: utf-8 -*-
from __future__ import annotations
"""
5168 實價登錄比價王 - 房仲店鋪底層純 API 爬取模組
完全不使用任何 DOM 解析，直接對接 5168 Web Service
"""

import os
import re
import time
import json
try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from turso_client import TursoClient
from address_utils import normalize_road_name, normalize_taiwan_address

TAIWAN_TZ = timezone(timedelta(hours=8))

def get_tw_now_str() -> str:
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

class HousePrice5168Crawler:
    BASE_SEARCH_URL = "https://buy.houseprice.tw/ws/BuyCaseList/{account}/{type}/Search/"
    BASE_OPTION_URL = "https://buy.houseprice.tw/ws/BuyCaseListOption/{account}/{type}/CityOption"
    
    DEFAULT_GAS_PROXY_URL = "https://script.google.com/macros/s/AKfycbxiytIWcthzj0tvttt9Jxvw7XwIqDW42upWjrJXcOKWZhnX8cY8H8pWQh5mDynaonhn/exec"
    IMPERSONATE_PROFILES = ["chrome124", "chrome120", "safari15_5"]

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    def __init__(self, db_client: Optional[TursoClient] = None, timeout: int = 15):
        self.db = db_client or TursoClient()
        self.timeout = timeout
        self.last_error: Optional[str] = None
        self.gas_proxy_url = os.environ.get("GAS_PROXY_URL") or self.DEFAULT_GAS_PROXY_URL
        self.session = self._create_session(0)

    def _create_session(self, profile_idx: int = 0):
        if USE_CURL_CFFI:
            prof = self.IMPERSONATE_PROFILES[profile_idx % len(self.IMPERSONATE_PROFILES)]
            return requests.Session(impersonate=prof)
        s = requests.Session()
        s.headers.update(self.DEFAULT_HEADERS)
        return s

    def _fetch_via_gas_proxy(self, target_url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
        """透過 Google Apps Script 中繼代理發送 GET 請求，繞過海外機房 IP 封鎖"""
        if not self.gas_proxy_url:
            return 0, ""
        try:
            payload = {
                "action": "proxy_fetch",
                "url": target_url,
                "headers": headers or self.DEFAULT_HEADERS
            }
            resp = self.session.post(
                self.gas_proxy_url,
                json=payload,
                timeout=max(self.timeout, 25),
                allow_redirects=True
            )
            if resp.status_code == 200:
                data = resp.json()
                if "status" in data and "body" in data:
                    return int(data["status"]), str(data["body"])
                return 0, f"GAS Proxy 格式異常 (請確認 GAS 已更新部署): {resp.text[:120]}"
            return resp.status_code, resp.text[:150]
        except Exception as e:
            return 0, str(e)

    @staticmethod
    def extract_account_info(input_str: str) -> Tuple[str, str]:
        """
        從使用者輸入的 5168 網址或字串提取帳號/手機與店鋪類型
        支援：
        1. 經紀人個人店鋪: https://realtor.houseprice.tw/agent/buy/0968682023/ -> ('0968682023', 'agent')
        2. 加盟店分店店鋪: https://realtor.houseprice.tw/store/buy/12345/ -> ('12345', 'store')
        3. 純手機號碼: 0968682023 -> ('0968682023', 'agent')
        """
        s = input_str.strip()
        m_agent = re.search(r'realtor\.houseprice\.tw/agent/buy/([0-9a-zA-Z_-]+)', s)
        if m_agent:
            return m_agent.group(1), "agent"

        m_store = re.search(r'realtor\.houseprice\.tw/store/buy/([0-9a-zA-Z_-]+)', s)
        if m_store:
            return m_store.group(1), "store"

        # 若是純手機號碼
        if re.match(r'^09\d{8}$', s):
            return s, "agent"

        # 通用數字或代號提取
        m_gen = re.search(r'/buy/([0-9a-zA-Z_-]+)', s)
        if m_gen:
            return m_gen.group(1), "agent"

        # 若都不符合但包含數字
        m_num = re.search(r'(\d{6,12})', s)
        if m_num:
            return m_num.group(1), "agent"

        raise ValueError(f"無法解析 5168 店鋪代號或網址: {input_str}")

    def fetch_all_houses(self, account: str, account_type: str = "agent") -> List[Dict[str, Any]]:
        self.last_error = None
        url = self.BASE_SEARCH_URL.format(account=account, type=account_type)
        shop_page_url = f"https://realtor.houseprice.tw/{account_type}/buy/{account}/"
        # 當使用 curl_cffi 時交由 impersonate 自動生成與 TLS 指紋一致的 User-Agent，避免衝突觸發 WAF
        headers = {
            "Referer": shop_page_url,
            "Accept": "application/json, text/plain, */*",
        }
        if not USE_CURL_CFFI:
            headers["User-Agent"] = self.DEFAULT_HEADERS["User-Agent"]

        all_raw_cases: List[Dict[str, Any]] = []
        page = 1
        total_pages = 1
        total_items = 0

        print(f"[*] [5168 API] 開始抓取 {account_type} ({account}) 物件清單 (curl_cffi={USE_CURL_CFFI})...")

        # 預熱店鋪主頁以取得合法 Session Cookie (降低海外機房 IP 直接請求 API 被擋機率)
        try:
            self.session.get(shop_page_url, timeout=10)
        except Exception:
            pass

        use_gas_proxy = False
        while page <= total_pages:
            page_success = False
            data = {}
            for attempt in range(1, 4):
                try:
                    if not use_gas_proxy:
                        resp = self.session.get(url, headers=headers, params={"p": page}, timeout=self.timeout)
                        status_code, resp_text = resp.status_code, resp.text
                    else:
                        gas_headers = {
                            "Referer": shop_page_url,
                            "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                            "Accept": "application/json, text/plain, */*",
                        }
                        status_code, resp_text = self._fetch_via_gas_proxy(f"{url}?p={page}", headers=gas_headers)

                    if status_code != 200:
                        self.last_error = f"HTTP {status_code}"
                        print(f"[!] 5168 第 {page} 頁請求失敗 ({'GAS代理' if use_gas_proxy else '直連'} HTTP {status_code}, 嘗試 {attempt}/3): {resp_text[:120]}")
                        # 若直連遭遇 403 阻擋，且未試過 GAS 中繼，立刻切換為 GAS 中繼代理
                        if not use_gas_proxy and status_code == 403 and self.gas_proxy_url:
                            print("[*] 偵測到 5168 直連遭 403 攔截，自動切換透過 GAS 中繼代理轉發...")
                            use_gas_proxy = True
                            continue
                        if attempt < 3:
                            if not use_gas_proxy:
                                self.session = self._create_session(attempt)
                                try:
                                    self.session.get(shop_page_url, timeout=10)
                                except Exception:
                                    pass
                            time.sleep(1.5 * attempt)
                            continue
                        break

                    data = json.loads(resp_text)
                    page_info = data.get("page", {})
                    total_items = page_info.get("totalItemCount", 0)
                    total_pages = page_info.get("totalPageCount", 1)

                    raw_cases = data.get("caseInfo", [])
                    if not raw_cases:
                        page_success = True
                        break

                    all_raw_cases.extend(raw_cases)
                    via_label = "(via GAS)" if use_gas_proxy else ""
                    print(f"[+] 5168 第 {page}/{total_pages} 頁抓取成功 {via_label}，累計 {len(all_raw_cases)}/{total_items} 筆")
                    page_success = True
                    break

                except Exception as e:
                    self.last_error = str(e)
                    print(f"[!] 5168 抓取第 {page} 頁異常 (嘗試 {attempt}/3): {e}")
                    if attempt < 3:
                        if not use_gas_proxy:
                            self.session = self._create_session(attempt)
                        time.sleep(1.5 * attempt)

            if not page_success or (page >= total_pages) or (not data.get("caseInfo")):
                break

            page += 1
            time.sleep(0.3)

        print(f"[OK] 5168 抓取完成！共收集 {len(all_raw_cases)} 筆原始在庫物件 (預期 {total_items} 筆)")
        
        # 雙渠道降級備援：若底層 API 取得 0 筆 (例如受海外 IP / 防火牆阻擋)，自動切換至 Nuxt 3 SSR 頁面備援
        if not all_raw_cases:
            print("[*] 5168 底層 API 未取得資料，自動啟動 Nuxt 3 SSR 雙渠道備援...")
            all_raw_cases = self.fetch_cases_from_ssr_nuxt(account, account_type)

        return all_raw_cases

    def fetch_cases_from_ssr_nuxt(self, account: str, account_type: str = "agent") -> List[Dict[str, Any]]:
        """
        雙渠道降級備援 (Solution A)：
        當 buy.houseprice.tw 底層 API 遭雲端機房/海外 IP 限制阻擋時，
        直接存取前端 Nuxt 3 SSR 頁面 (https://realtor.houseprice.tw/{type}/buy/{account}/)，
        解析 HTML 中的 __NUXT_DATA__ 結構化資料，提取在售物件清單。
        """
        ssr_url = f"https://realtor.houseprice.tw/{account_type}/buy/{account}/"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if not USE_CURL_CFFI:
            headers["User-Agent"] = self.DEFAULT_HEADERS["User-Agent"]

        html_text = ""
        for attempt in range(1, 3):
            try:
                print(f"[*] [5168 SSR] 嘗試從前端 Nuxt SSR ({ssr_url}) 提取資料 (嘗試 {attempt}/2)...")
                resp = self.session.get(ssr_url, headers=headers, timeout=self.timeout)
                if resp.status_code == 200:
                    html_text = resp.text
                    break
                self.last_error = f"SSR HTTP {resp.status_code}"
                print(f"[!] 5168 SSR 頁面請求失敗 (HTTP {resp.status_code})")
                if attempt < 2:
                    self.session = self._create_session(attempt)
                    time.sleep(1.5)
            except Exception as e:
                self.last_error = str(e)
                print(f"[!] 5168 SSR 請求異常 (嘗試 {attempt}/2): {e}")
                if attempt < 2:
                    self.session = self._create_session(attempt)
                    time.sleep(1.5)

        if not html_text and self.gas_proxy_url:
            print("[*] [5168 SSR] 直連失敗，嘗試透過 GAS 中繼代理抓取 SSR 頁面...")
            g_status, g_body = self._fetch_via_gas_proxy(ssr_url, headers={"User-Agent": self.DEFAULT_HEADERS["User-Agent"]})
            if g_status == 200 and g_body:
                html_text = g_body
                self.last_error = None
            elif g_status:
                self.last_error = f"SSR GAS Proxy HTTP {g_status}"

        if not html_text:
            return []

        try:
            m = re.search(r'<script[^>]*id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>', html_text, re.DOTALL)
            if not m:
                print(f"[!] 5168 SSR 頁面中未找到 __NUXT_DATA__ 標籤")
                return []

            data = json.loads(m.group(1).strip())

            def parse_node(idx, visited=None):
                if visited is None:
                    visited = set()
                if idx is None or not (0 <= idx < len(data)):
                    return idx
                if idx in visited:
                    return None
                visited.add(idx)
                raw = data[idx]
                if isinstance(raw, (str, float, bool)) or raw is None:
                    return raw
                elif isinstance(raw, int):
                    return raw
                elif isinstance(raw, list):
                    return [parse_node(child_idx, visited.copy()) for child_idx in raw]
                elif isinstance(raw, dict):
                    res_dict = {}
                    for k, v_idx in raw.items():
                        if isinstance(v_idx, int) and 0 <= v_idx < len(data):
                            res_dict[k] = parse_node(v_idx, visited.copy())
                        else:
                            res_dict[k] = v_idx
                    return res_dict
                return raw

            cases = []
            for item in data:
                if isinstance(item, dict) and "caseInfo" in item:
                    case_info_idx = item["caseInfo"]
                    if isinstance(case_info_idx, int) and 0 <= case_info_idx < len(data):
                        case_list_raw = data[case_info_idx]
                        if isinstance(case_list_raw, list):
                            for c_idx in case_list_raw:
                                c_parsed = parse_node(c_idx)
                                if isinstance(c_parsed, dict) and "sid" in c_parsed:
                                    cases.append(c_parsed)
                    break

            print(f"✅ [5168 SSR] 成功從 Nuxt SSR 備援渠道提取 {len(cases)} 筆在售物件！")
            return cases
        except Exception as e:
            print(f"[!] 5168 SSR 備援解析異常: {e}")
            return []

    def save_to_turso(self, account: str, account_type: str, raw_cases: List[Dict[str, Any]]):
        """將原始 5168 案件保存至 t5168_shops 與 t5168_houses 快照表"""
        now_str = get_tw_now_str()
        agent_name = raw_cases[0].get("agentName", "") if raw_cases else ""
        shop_title = f"{agent_name} 的 5168 店鋪" if agent_name else f"5168 店鋪 {account}"

        # 1. 更新店鋪資料
        self.db.execute(
            """
            INSERT INTO t5168_shops (account, shop_type, agent_name, shop_title, sale_count, last_checked_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account) DO UPDATE SET
                shop_type=excluded.shop_type,
                agent_name=excluded.agent_name,
                shop_title=excluded.shop_title,
                sale_count=excluded.sale_count,
                last_checked_at=excluded.last_checked_at;
            """,
            [account, account_type, agent_name, shop_title, len(raw_cases), now_str]
        )

        # 1.5 僅在有成功獲取到案件時清理既有快照，避免抓取網路中斷時誤刪備份
        if raw_cases:
            self.db.execute("DELETE FROM t5168_houses WHERE account = ?;", [account])
        else:
            print(f"[Warn] 5168 本次未抓取到物件，保留現有 Turso 快照不作刪除。")
            return

        # 2. 批次更新物件快照
        upsert_sql = """
            INSERT INTO t5168_houses (
                house_id, account, title, price, reg_area, main_area,
                floor, all_floor, room, county, district, street,
                community_name, kind_name, case_type, house_age, cover, url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(house_id) DO UPDATE SET
                account=excluded.account,
                title=excluded.title,
                price=excluded.price,
                reg_area=excluded.reg_area,
                main_area=excluded.main_area,
                floor=excluded.floor,
                all_floor=excluded.all_floor,
                room=excluded.room,
                county=excluded.county,
                district=excluded.district,
                street=excluded.street,
                community_name=excluded.community_name,
                kind_name=excluded.kind_name,
                case_type=excluded.case_type,
                house_age=excluded.house_age,
                cover=excluded.cover,
                url=excluded.url,
                updated_at=excluded.updated_at;
        """

        batch_stmts = []
        for c in raw_cases:
            hid = str(c.get("sid"))
            comm = c.get("communityInfo") or {}
            pic = c.get("pictureInfo") or {}

            fl = c.get("fromFloor") or 0
            allfl = c.get("totalFloor") or 0

            # 若型態為透天/別墅或整棟，樓層轉為 99
            case_type = c.get("caseType") or ""
            if any(t in case_type for t in ["透天", "別墅", "整棟"]):
                fl = 99

            raw_age = float(c.get("buildAge") or 0.0) if c.get("buildAge") is not None else 0.0

            params = [
                hid,
                account,
                c.get("caseName") or "",
                float(c.get("totalPrice") or 0.0),
                float(c.get("buildPin") or 0.0),
                float(c.get("mainPin") or 0.0) if c.get("mainPin") is not None else None,
                int(fl),
                int(allfl),
                int(c.get("rm") or 0),
                c.get("city") or "",
                c.get("district") or "",
                normalize_road_name(c.get("road") or ""),
                comm.get("communityName") or "",
                c.get("purposeName") or "",
                case_type,
                raw_age,
                pic.get("coverUrl") or "",
                c.get("caseUrl") or f"https://buy.houseprice.tw/house/{hid}/",
                now_str
            ]
            batch_stmts.append((upsert_sql, params))
            if len(batch_stmts) >= 50:
                self.db.batch(batch_stmts)
                batch_stmts = []

        if batch_stmts:
            self.db.batch(batch_stmts)

    @staticmethod
    def to_standard_houses(raw_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        將 5168 原始資料轉換為跨平台通用 StandardHouse 規格
        """
        standard_list = []
        for c in raw_cases:
            hid = str(c.get("sid"))
            comm = c.get("communityInfo") or {}
            pic = c.get("pictureInfo") or {}

            fl = c.get("fromFloor") or 0
            allfl = c.get("totalFloor") or 0
            case_type = c.get("caseType") or ""

            # 若型態為透天/別墅且 floor 為 0 或 1，相容標為 99 (整棟)
            if any(t in case_type for t in ["透天", "別墅"]):
                if fl in [0, 1, 99]:
                    fl = 99

            # 判斷是否為土地 (依 5168 底層 API 結構化欄位判斷：caseType 或 purposeName 為土地，絕不透過標題 caseName 判斷)
            purpose = c.get("purposeName") or ""
            is_land = any(k == case_type for k in ["土地", "農地", "建地", "林地", "山坡地", "工商業地"]) or (purpose == "土地")
            pin_val = float(c.get("buildPin") or c.get("landPin") or 0.0)
            if is_land:
                case_type = "土地"

            raw_age = float(c.get("buildAge") or 0.0) if c.get("buildAge") is not None else 0.0

            std_item = {
                "platform": "5168",
                "house_id": hid,
                "title": c.get("caseName") or "",
                "url": c.get("caseUrl") or f"https://buy.houseprice.tw/house/{hid}/",
                "county": (c.get("city") or "").strip(),
                "district": (c.get("district") or "").strip(),
                "street": normalize_road_name((c.get("road") or "").strip()),
                "community_name": (comm.get("communityName") or "").strip(),
                "price": float(c.get("totalPrice") or 0.0),
                "reg_area": pin_val,
                "land_area": pin_val,
                "is_land": is_land,
                "main_area": float(c.get("mainPin") or 0.0) if c.get("mainPin") is not None else None,
                "floor": int(fl),
                "all_floor": int(allfl),
                "house_age": raw_age,
                "room": int(c.get("rm") or 0),
                "case_type": case_type,
                "cover": pic.get("coverUrl") or "",
                "agent_name": c.get("agentName") or ""
            }
            standard_list.append(std_item)
        return standard_list

    def crawl_and_get_standard_houses(self, shop_input: str) -> Tuple[str, str, List[Dict[str, Any]]]:
        """
        爬取 5168 店鋪，持久化至 Turso，並回傳 (account, full_shop_url, standard_houses)
        """
        account, account_type = self.extract_account_info(shop_input)
        full_shop_url = f"https://realtor.houseprice.tw/{account_type}/buy/{account}/"
        print(f"\n================ 開始抓取 5168 店鋪 {account} ({account_type}) ================")
        raw_cases = self.fetch_all_houses(account, account_type)
        if raw_cases:
            print(f"共抓取 {len(raw_cases)} 筆 5168 在售庫存，正在儲存至 Turso...")
            self.save_to_turso(account, account_type, raw_cases)
            print(f"✅ 5168 店鋪 {account} 快照儲存完成！")
            std_houses = self.to_standard_houses(raw_cases)
        else:
            print(f"[Warn] 5168 線上抓取筆數為 0，嘗試從 Turso 資料庫載入既有快照...")
            db_rows = self.db.execute("SELECT * FROM t5168_houses WHERE account = ?;", [account])
            if db_rows:
                print(f"[+] 成功從 Turso 資料庫快照載入 {len(db_rows)} 筆既有物件！")
                std_houses = []
                for r in db_rows:
                    std_houses.append({
                        "platform": "5168",
                        "house_id": str(r.get("house_id")),
                        "title": r.get("title") or "",
                        "url": r.get("url") or f"https://buy.houseprice.tw/house/{r.get('house_id')}/",
                        "county": (r.get("county") or "").strip(),
                        "district": (r.get("district") or "").strip(),
                        "street": (r.get("street") or "").strip(),
                        "community_name": (r.get("community_name") or "").strip(),
                        "price": float(r.get("price") or 0.0),
                        "reg_area": float(r.get("reg_area") or 0.0),
                        "main_area": float(r.get("main_area") or 0.0) if r.get("main_area") is not None else None,
                        "floor": int(r.get("floor") or 0),
                        "all_floor": int(r.get("all_floor") or 0),
                        "house_age": float(r.get("house_age") or 0.0),
                        "room": int(r.get("room") or 0),
                        "case_type": r.get("case_type") or "",
                        "cover": r.get("cover") or ""
                    })
            else:
                std_houses = []
        return account, full_shop_url, std_houses

if __name__ == "__main__":
    crawler = HousePrice5168Crawler()
    acc, url, houses = crawler.crawl_and_get_standard_houses("https://realtor.houseprice.tw/agent/buy/0968682023/")
    print(f"抓取完成！取得 {len(houses)} 筆標準物件，首筆：")
    if houses:
        print(json.dumps(houses[0], ensure_ascii=False, indent=2))
