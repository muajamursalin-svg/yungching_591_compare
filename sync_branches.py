import os
import re
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from turso_client import TursoClient

TAIWAN_TZ = timezone(timedelta(hours=8))

def get_tw_now_str() -> str:
    """取得標準台灣時間字串 (UTC+8)"""
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

def parse_taiwan_address(raw_addr: str) -> tuple:
    if not raw_addr:
        return "", "", ""
    m = re.match(r'^(.*?([縣市]))(.*?([區鄉鎮市]))(.*)$', raw_addr)
    if m:
        return m.group(1).strip(), m.group(3).strip(), m.group(5).strip()
    county = raw_addr[:3] if len(raw_addr) >= 3 else ""
    district = raw_addr[3:6] if len(raw_addr) >= 6 else ""
    return county, district, raw_addr[6:]

class YungchingBranchSyncer:
    BASE_DOMAIN = "https://shop.yungching.com.tw"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://shop.yungching.com.tw/",
        "Accept": "application/json, text/plain, */*",
    }

    def __init__(self, db_client: TursoClient):
        self.db = db_client

    def acquire_sync_lock(self):
        """啟動分店同步鎖定 (防併發查詢，通知使用者正在刷新庫存)"""
        now_str = get_tw_now_str()
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS system_locks (
                lock_key TEXT PRIMARY KEY,
                is_locked INTEGER DEFAULT 0,
                locked_at TEXT,
                updated_at TEXT,
                message TEXT
            );
            """
        )
        self.db.execute(
            """
            INSERT INTO system_locks (lock_key, is_locked, locked_at, updated_at, message)
            VALUES ('branch_sync', 1, ?, ?, '目前刷新分店庫存中 請五分鐘後再查詢')
            ON CONFLICT(lock_key) DO UPDATE SET
                is_locked = 1,
                locked_at = excluded.locked_at,
                updated_at = excluded.updated_at,
                message = excluded.message;
            """,
            [now_str, now_str]
        )
        print(f"🔒 [Lock] 已啟動系統維護鎖定 (is_locked=1) @ {now_str}")

    def release_sync_lock(self):
        """解除分店同步鎖定"""
        now_str = get_tw_now_str()
        try:
            self.db.execute(
                """
                UPDATE system_locks 
                SET is_locked = 0, updated_at = ? 
                WHERE lock_key = 'branch_sync';
                """,
                [now_str]
            )
            print(f"🔓 [Lock] 已解除系統鎖定 (is_locked=0) @ {now_str}")
        except Exception as e:
            print(f"[Error] 解除系統鎖定失敗: {e}")

    def fetch_shop_info(self, branch_id: str) -> dict:
        url = f"{self.BASE_DOMAIN}/api/v1/shop/base"
        try:
            res = requests.get(url, params={"index": branch_id}, headers=self.HEADERS, timeout=10)
            res.raise_for_status()
            data = res.json().get("data", {})
            return {
                "branch_id": branch_id,
                "name": f"{data.get('headName', '')} {data.get('shopName', '')}".strip() or branch_id,
                "brand": data.get("headName", ""),
                "company_name": data.get("companyName", ""),
                "county": data.get("county", ""),
                "district": data.get("district", ""),
                "address": data.get("address", ""),
                "phone": data.get("phone", ""),
            }
        except Exception as e:
            print(f"[Warn] 無法取得分店 {branch_id} 基本資料: {e}")
            return {
                "branch_id": branch_id,
                "name": branch_id,
                "brand": "",
                "company_name": "",
                "county": "",
                "district": "",
                "address": "",
                "phone": ""
            }

    def fetch_branch_inventory(self, branch_id: str) -> list:
        url = f"{self.BASE_DOMAIN}/api/v1/house/caselist"
        all_houses = []
        page = 1
        page_size = 100

        while True:
            params = {
                "shop": branch_id,
                "pg": page,
                "ps": page_size,
                "pinType": 0,
                "isAddRoom": "true"
            }
            res = requests.get(url, params=params, headers=self.HEADERS, timeout=15)
            res.raise_for_status()
            data = res.json().get("data", {})
            items = data.get("list", [])
            pa = data.get("pa", {})
            total_pages = pa.get("totalPageCount", 1)

            if not items:
                break

            all_houses.extend(items)
            print(f"  [分店 {branch_id}] 第 {page}/{total_pages} 頁，累積拉取 {len(all_houses)}/{pa.get('totalItemCount', 0)} 筆")

            if page >= total_pages:
                break
            page += 1
            time.sleep(0.3)

        return all_houses

    def sync_branch(self, branch_cfg: dict):
        branch_id = branch_cfg["branch_id"]
        now_str = get_tw_now_str()
        print(f"\n================ 開始同步分店: {branch_cfg.get('name', branch_id)} ({branch_id}) ================")

        # 1. 取得並更新分店基本資訊
        info = self.fetch_shop_info(branch_id)
        self.db.execute(
            """
            INSERT INTO branches (branch_id, name, brand, company_name, county, district, address, phone, enabled, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(branch_id) DO UPDATE SET
                name=excluded.name,
                brand=excluded.brand,
                company_name=excluded.company_name,
                county=excluded.county,
                district=excluded.district,
                address=excluded.address,
                phone=excluded.phone,
                last_synced_at=excluded.last_synced_at;
            """,
            [branch_id, info["name"], info["brand"], info["company_name"], info["county"], info["district"], info["address"], info["phone"], now_str]
        )

        # 2. 爬取該分店所有最新庫存
        items = self.fetch_branch_inventory(branch_id)
        if not items:
            print(f"  [分店 {branch_id}] 無在售物件資料。")
            return

        print(f"  正在寫入 Turso 資料庫 (共 {len(items)} 筆物件)...")
        active_sids = []
        batch_stmts = []

        upsert_sql = """
            INSERT INTO yungching_houses (
                case_sid, branch_id, case_key, show_case_no, case_name, case_type, purpose,
                county, district, address, community_id, community_name, price, last_price,
                reg_area, main_area, land_area, from_floor, to_floor, up_floor,
                room, living_room, bath_room, parking, build_age, case_feature, cover,
                is_exclusive, is_discount, tag, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?
            ) ON CONFLICT(case_sid) DO UPDATE SET
                branch_id=excluded.branch_id,
                case_key=excluded.case_key,
                show_case_no=excluded.show_case_no,
                case_name=excluded.case_name,
                case_type=excluded.case_type,
                purpose=excluded.purpose,
                county=excluded.county,
                district=excluded.district,
                address=excluded.address,
                community_id=excluded.community_id,
                community_name=excluded.community_name,
                price=excluded.price,
                last_price=excluded.last_price,
                reg_area=excluded.reg_area,
                main_area=excluded.main_area,
                land_area=excluded.land_area,
                from_floor=excluded.from_floor,
                to_floor=excluded.to_floor,
                up_floor=excluded.up_floor,
                room=excluded.room,
                living_room=excluded.living_room,
                bath_room=excluded.bath_room,
                parking=excluded.parking,
                build_age=excluded.build_age,
                case_feature=excluded.case_feature,
                cover=excluded.cover,
                is_exclusive=excluded.is_exclusive,
                is_discount=excluded.is_discount,
                tag=excluded.tag,
                updated_at=excluded.updated_at;
        """

        for h in items:
            case_sid = int(h.get("caseSId"))
            active_sids.append(case_sid)
            
            # 地址拆分縣市行政區
            raw_addr = h.get("address", "")
            county, district, _ = parse_taiwan_address(raw_addr)

            pin = h.get("pinInfo") or {}
            fl = h.get("floorInfo") or {}
            pat = h.get("patternInfo") or {}
            comm = h.get("communityInfo") or {}
            lbl = h.get("labelsInfo") or {}

            params = [
                case_sid,
                branch_id,
                h.get("caseKey"),
                h.get("showCaseNo"),
                h.get("caseName") or "",
                h.get("caseTypeName") or "",
                h.get("purposeName") or "",
                county,
                district,
                raw_addr,
                comm.get("communityId"),
                comm.get("communityName") or "",
                float(h.get("price", 0)) if h.get("price") is not None else 0.0,
                float(h.get("lastPrice", 0)) if h.get("lastPrice") is not None else 0.0,
                float(pin.get("regArea", 0)) if pin.get("regArea") is not None else 0.0,
                float(pin.get("mainArea", 0)) if pin.get("mainArea") is not None else 0.0,
                float(pin.get("landArea", 0)) if pin.get("landArea") is not None else 0.0,
                fl.get("fromFloor"),
                fl.get("toFloor"),
                fl.get("upFloor"),
                pat.get("room"),
                pat.get("livingRoom"),
                pat.get("bathRoom"),
                h.get("parking") or "",
                float(h.get("buildAge", 0)) if h.get("buildAge") is not None else 0.0,
                h.get("caseFeature") or "",
                h.get("cover") or "",
                1 if lbl.get("isExclusive") else 0,
                1 if h.get("isDiscount") else 0,
                json.dumps(h.get("tag", []), ensure_ascii=False),
                now_str
            ]
            batch_stmts.append((upsert_sql, params))

            # 每 50 筆批次寫入一次
            if len(batch_stmts) >= 50:
                self.db.batch(batch_stmts)
                batch_stmts = []

        if batch_stmts:
            self.db.batch(batch_stmts)

        print(f"✅ 分店 {branch_id} 同步完成！在庫物件數量: {len(active_sids)}")
        return len(active_sids)

    def run(self, branches_file="branches.json"):
        cfg_path = os.path.join(os.path.dirname(__file__), branches_file)
        if not os.path.exists(cfg_path):
            raise FileNotFoundError(f"找不到分店設定檔: {cfg_path}")

        with open(cfg_path, "r", encoding="utf-8") as f:
            branches = json.load(f)

        enabled_branches = [b for b in branches if b.get("enabled", True)]
        print(f"載入 {len(branches)} 家分店設定，其中 {len(enabled_branches)} 家啟用同步...")

        # 1. 啟動系統同步鎖定 (防併發比對查詢)
        self.acquire_sync_lock()

        try:
            # 2. 清空既有分店庫存總表，徹底根除下架/過期舊資料殘留問題
            print("🧹 清空舊有永慶分店庫存 (DELETE FROM yungching_houses)...")
            self.db.execute("DELETE FROM yungching_houses;")
            print("✅ 舊有庫存已清空，開始向各分店拉取最新在庫資料並填入...")

            total_synced_count = 0
            for b in enabled_branches:
                count = self.sync_branch(b)
                total_synced_count += (count or 0)

            print(f"\n🎉 所有分店同步作業完成！目前永慶分店在庫總物件數: {total_synced_count}")

        finally:
            # 3. 確保無論成功或發生例外，必定解除鎖定
            self.release_sync_lock()

        # 4. 自動觸發定期資料清理 (保留最近 3 天比對與快照紀錄)
        try:
            from cleanup_task import run_cleanup
            run_cleanup(retention_days=3, db_client=self.db)
        except Exception as e:
            print(f"[Warn] 觸發資料清理失敗: {e}")

if __name__ == "__main__":
    db = TursoClient()
    syncer = YungchingBranchSyncer(db)
    syncer.run()
