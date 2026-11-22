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
        """記錄分店同步啟動 (增量更新架構，不阻擋使用者即時查詢，is_locked=0)"""
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
            VALUES ('branch_sync', 0, ?, ?, '分店庫存同步中 (增量無縫更新，可正常查詢)')
            ON CONFLICT(lock_key) DO UPDATE SET
                is_locked = 0,
                locked_at = excluded.locked_at,
                updated_at = excluded.updated_at,
                message = excluded.message;
            """,
            [now_str, now_str]
        )
        print(f"🔄 [Sync] 已記錄同步開始時間 @ {now_str} (維持無鎖狀態，允許即時查詢)")

    def release_sync_lock(self):
        """解除/更新分店同步完成記錄"""
        now_str = get_tw_now_str()
        try:
            self.db.execute(
                """
                UPDATE system_locks 
                SET is_locked = 0, updated_at = ?, message = '分店庫存同步完成'
                WHERE lock_key = 'branch_sync';
                """,
                [now_str]
            )
            print(f"✅ [Sync] 同步任務結束 @ {now_str}")
        except Exception as e:
            print(f"[Error] 更新系統鎖定狀態失敗: {e}")

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
        branch_name = branch_cfg.get("name") or info.get("name") or branch_id
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
            [branch_id, branch_name, info["brand"], info["company_name"], info["county"], info["district"], info["address"], info["phone"], now_str]
        )

        # 2. 爬取該分店所有最新庫存
        items = self.fetch_branch_inventory(branch_id)
        if not items:
            print(f"  [分店 {branch_id}] 無在售物件資料 (或網路取得為空，跳過下架更新以防誤刪)。")
            return 0

        # 3. 取得目前該分店在資料庫中的所有物件快照 (Per-branch 範圍)
        db_rows = self.db.execute(
            "SELECT * FROM yungching_houses WHERE branch_id = ?;",
            [branch_id]
        )
        db_map = {int(r["case_sid"]): r for r in db_rows if r.get("case_sid") is not None}

        # 4. 比對與分類
        to_insert_stmts = []
        to_update_stmts = []
        price_log_stmts = []
        incoming_sids = set()
        unchanged_count = 0

        insert_sql = """
            INSERT INTO yungching_houses (
                case_sid, branch_id, case_key, show_case_no, case_name, case_type, purpose,
                county, district, address, community_id, community_name, price, last_price,
                reg_area, main_area, land_area, from_floor, to_floor, up_floor,
                room, living_room, bath_room, parking, build_age, case_feature, cover,
                is_exclusive, is_discount, tag, status, online_at, offline_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, 'ONLINE', ?, NULL, ?
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
                status='ONLINE',
                online_at=excluded.online_at,
                offline_at=NULL,
                updated_at=excluded.updated_at;
        """

        update_sql = """
            UPDATE yungching_houses SET
                case_key = ?, show_case_no = ?, case_name = ?, case_type = ?, purpose = ?,
                county = ?, district = ?, address = ?, community_id = ?, community_name = ?,
                price = ?, last_price = ?, reg_area = ?, main_area = ?, land_area = ?,
                from_floor = ?, to_floor = ?, up_floor = ?,
                room = ?, living_room = ?, bath_room = ?, parking = ?, build_age = ?,
                case_feature = ?, cover = ?, is_exclusive = ?, is_discount = ?, tag = ?,
                status = 'ONLINE', offline_at = NULL, updated_at = ?
            WHERE case_sid = ?;
        """

        price_log_sql = """
            INSERT INTO house_price_logs (
                case_sid, branch_id, branch_name, case_name, county, district, address,
                community_name, reg_area, cover, old_price, new_price, price_diff, percent_change, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """

        for h in items:
            case_sid = int(h.get("caseSId"))
            incoming_sids.add(case_sid)

            raw_addr = (h.get("address") or "").strip()
            county, district, _ = parse_taiwan_address(raw_addr)

            pin = h.get("pinInfo") or {}
            fl = h.get("floorInfo") or {}
            pat = h.get("patternInfo") or {}
            comm = h.get("communityInfo") or {}
            lbl = h.get("labelsInfo") or {}

            price_val = float(h.get("price", 0)) if h.get("price") is not None else 0.0
            last_price_val = float(h.get("lastPrice", 0)) if h.get("lastPrice") is not None else 0.0
            reg_area_val = float(pin.get("regArea", 0)) if pin.get("regArea") is not None else 0.0
            main_area_val = float(pin.get("mainArea", 0)) if pin.get("mainArea") is not None else 0.0
            land_area_val = float(pin.get("landArea", 0)) if pin.get("landArea") is not None else 0.0
            from_floor_val = fl.get("fromFloor")
            to_floor_val = fl.get("toFloor")
            up_floor_val = fl.get("upFloor")
            room_val = pat.get("room")
            living_val = pat.get("livingRoom")
            bath_val = pat.get("bathRoom")
            age_val = float(h.get("buildAge", 0)) if h.get("buildAge") is not None else 0.0
            parking_val = (h.get("parking") or "").strip()
            case_name_val = (h.get("caseName") or "").strip()
            case_type_val = (h.get("caseTypeName") or "").strip()
            purpose_val = (h.get("purposeName") or "").strip()
            comm_name_val = (comm.get("communityName") or "").strip()
            comm_id_val = comm.get("communityId")
            cover_val = (h.get("cover") or "").strip()
            feature_val = (h.get("caseFeature") or "").strip()
            show_case_no_val = (h.get("showCaseNo") or "").strip()
            case_key_val = (h.get("caseKey") or "").strip()
            exclusive_val = 1 if lbl.get("isExclusive") else 0
            discount_val = 1 if h.get("isDiscount") else 0
            tag_val = json.dumps(h.get("tag", []), ensure_ascii=False)

            if case_sid not in db_map:
                # 新增上架
                to_insert_stmts.append((insert_sql, [
                    case_sid, branch_id, case_key_val, show_case_no_val, case_name_val, case_type_val, purpose_val,
                    county, district, raw_addr, comm_id_val, comm_name_val, price_val, last_price_val,
                    reg_area_val, main_area_val, land_area_val, from_floor_val, to_floor_val, up_floor_val,
                    room_val, living_val, bath_val, parking_val, age_val, feature_val, cover_val,
                    exclusive_val, discount_val, tag_val, now_str, now_str
                ]))
            else:
                existing = db_map[case_sid]

                # 判斷是否有任何欄位資料更動
                is_diff = False
                old_price = round(float(existing.get("price") or 0.0), 2)
                cur_price = round(price_val, 2)
                if old_price != cur_price:
                    is_diff = True
                elif round(float(existing.get("reg_area") or 0.0), 2) != round(reg_area_val, 2):
                    is_diff = True
                elif round(float(existing.get("main_area") or 0.0), 2) != round(main_area_val, 2):
                    is_diff = True
                elif round(float(existing.get("land_area") or 0.0), 2) != round(land_area_val, 2):
                    is_diff = True
                elif int(existing.get("from_floor") or 0) != int(from_floor_val or 0):
                    is_diff = True
                elif int(existing.get("to_floor") or 0) != int(to_floor_val or 0):
                    is_diff = True
                elif int(existing.get("up_floor") or 0) != int(up_floor_val or 0):
                    is_diff = True
                elif round(float(existing.get("room") or 0.0), 1) != round(float(room_val or 0.0), 1):
                    is_diff = True
                elif round(float(existing.get("living_room") or 0.0), 1) != round(float(living_val or 0.0), 1):
                    is_diff = True
                elif round(float(existing.get("bath_room") or 0.0), 1) != round(float(bath_val or 0.0), 1):
                    is_diff = True
                elif round(float(existing.get("build_age") or 0.0), 2) != round(age_val, 2):
                    is_diff = True
                elif (existing.get("case_name") or "").strip() != case_name_val:
                    is_diff = True
                elif (existing.get("address") or "").strip() != raw_addr:
                    is_diff = True
                elif (existing.get("community_name") or "").strip() != comm_name_val:
                    is_diff = True
                elif (existing.get("parking") or "").strip() != parking_val:
                    is_diff = True
                elif (existing.get("case_type") or "").strip() != case_type_val:
                    is_diff = True
                elif (existing.get("cover") or "").strip() != cover_val:
                    is_diff = True
                elif (existing.get("case_feature") or "").strip() != feature_val:
                    is_diff = True
                elif (existing.get("status") or "") != "ONLINE":
                    is_diff = True

                if is_diff:
                    # 資料有變 -> 整行更新覆蓋
                    to_update_stmts.append((update_sql, [
                        case_key_val, show_case_no_val, case_name_val, case_type_val, purpose_val,
                        county, district, raw_addr, comm_id_val, comm_name_val,
                        price_val, last_price_val, reg_area_val, main_area_val, land_area_val,
                        from_floor_val, to_floor_val, up_floor_val,
                        room_val, living_val, bath_val, parking_val, age_val,
                        feature_val, cover_val, exclusive_val, discount_val, tag_val,
                        now_str, case_sid
                    ]))

                    # 若開價有變動 -> 記錄至價格歷史日誌表
                    if old_price > 0 and cur_price > 0 and abs(old_price - cur_price) >= 0.01:
                        p_diff = round(cur_price - old_price, 2)
                        p_pct = round((p_diff / old_price) * 100, 2)
                        price_log_stmts.append((price_log_sql, [
                            case_sid, branch_id, branch_name, case_name_val,
                            county, district, raw_addr, comm_name_val,
                            reg_area_val, cover_val, old_price, cur_price,
                            p_diff, p_pct, now_str
                        ]))
                else:
                    unchanged_count += 1

        # 5. 判定本次下架名單 (DB 有且為 ONLINE，但本次爬取未出現)
        to_deactivate_stmts = []
        deactivate_sql = "UPDATE yungching_houses SET status = 'OFFLINE', offline_at = ? WHERE case_sid = ?;"
        for sid, ex in db_map.items():
            if sid not in incoming_sids:
                if (ex.get("status") or "") != "OFFLINE":
                    to_deactivate_stmts.append((deactivate_sql, [now_str, sid]))

        # 6. 批次提交變更至 Turso (分段 batch 寫入)
        all_batch = to_insert_stmts + to_update_stmts + to_deactivate_stmts + price_log_stmts
        if all_batch:
            print(f"  ⚡ [分店 {branch_id}] 執行差分更新：新上架 {len(to_insert_stmts)} 筆，欄位更動 {len(to_update_stmts)} 筆 (其中改價 {len(price_log_stmts)} 筆)，下架 {len(to_deactivate_stmts)} 筆...")
            chunk_size = 50
            for i in range(0, len(all_batch), chunk_size):
                self.db.batch(all_batch[i:i + chunk_size])
        else:
            print(f"  ✨ [分店 {branch_id}] 全數資料皆為最新，無須寫入資料庫。")

        print(f"✅ 分店 {branch_id} 同步完成！在庫: {len(incoming_sids)} 筆 (無變動 {unchanged_count} 筆)")
        return len(incoming_sids)

    def run(self, branches_file="branches.json"):
        cfg_path = os.path.join(os.path.dirname(__file__), branches_file)
        if not os.path.exists(cfg_path):
            raise FileNotFoundError(f"找不到分店設定檔: {cfg_path}")

        with open(cfg_path, "r", encoding="utf-8") as f:
            branches = json.load(f)

        enabled_branches = [b for b in branches if b.get("enabled", True)]
        print(f"載入 {len(branches)} 家分店設定，其中 {len(enabled_branches)} 家啟用同步...")

        # 1. 記錄同步狀態 (增量無鎖)
        self.acquire_sync_lock()

        try:
            total_active_count = 0
            for b in enabled_branches:
                count = self.sync_branch(b)
                total_active_count += (count or 0)

            print(f"\n🎉 所有分店同步作業完成！目前永慶分店在庫總物件數: {total_active_count}")

        finally:
            # 2. 標記同步結束
            self.release_sync_lock()

        # 3. 自動觸發定期資料清理 (保留最近 3 天比對與快照紀錄)
        try:
            from cleanup_task import run_cleanup
            run_cleanup(retention_days=3, db_client=self.db)
        except Exception as e:
            print(f"[Warn] 觸發資料清理失敗: {e}")

if __name__ == "__main__":
    db = TursoClient()
    syncer = YungchingBranchSyncer(db)
    syncer.run()
