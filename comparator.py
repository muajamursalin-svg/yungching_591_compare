# -*- coding: utf-8 -*-
from __future__ import annotations
"""
通用房仲庫存比對核心模組 (Unified Multi-Platform Inventory Comparator)
支援 591、5168、樂屋網 等任意平台庫存與永慶多加盟店在庫物件之精準特徵比對。
未來只要在此模組微調比對條件或容許閾值，所有平台皆同步生效！
"""

import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from turso_client import TursoClient
from geo_utils import is_road_too_far

# 統一使用台灣時區 (UTC+8)
TAIWAN_TZ = timezone(timedelta(hours=8))

def get_tw_now() -> datetime:
    return datetime.now(TAIWAN_TZ)

def get_tw_now_str() -> str:
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

def clean_text(s: str) -> str:
    if not s:
        return ""
    # 移除 HTML 標籤
    s = re.sub(r'<[^>]+>', '', s)
    # 統一「臺」與「台」
    s = s.replace('臺', '台')
    # 移除特殊標點符號與多餘空白
    s = re.sub(r'[\s\-_\/|~#🌸❤️⭐️✨【】\(\)（）\.\,，、]', '', s)
    return s.strip()

class InventoryComparator:
    """
    通用庫存比對引擎：
    - 將外部平台（591, 5168, 樂屋網）的物件標準化後與分店（永慶）資料庫比對
    - 提供快取檢驗、兩階段特徵硬過濾、多維診斷與報告生成
    """
    def __init__(self, db_client: Optional[TursoClient] = None):
        self.db = db_client or TursoClient()

    def check_branch_sync_lock(self, timeout_minutes: int = 15) -> Optional[Dict[str, Any]]:
        """
        檢查系統是否正處於分店庫存刷新鎖定中
        若鎖定超過 timeout_minutes (預設 15 分鐘)，視為超時鎖失效，避免死鎖
        """
        try:
            rows = self.db.execute("SELECT * FROM system_locks WHERE lock_key = 'branch_sync' LIMIT 1;")
            if not rows:
                return None
            lock = rows[0]
            if int(lock.get("is_locked") or 0) == 1:
                locked_at_str = lock.get("locked_at")
                if locked_at_str:
                    try:
                        locked_time = datetime.strptime(locked_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIWAN_TZ)
                        if (get_tw_now() - locked_time).total_seconds() > timeout_minutes * 60:
                            print(f"[Warn] 偵測到超時的同步鎖 ({locked_at_str})，已自動忽略。")
                            return None
                    except Exception:
                        pass
                return lock
        except Exception as e:
            print(f"[Warn] 查詢系統鎖定狀態失敗: {e}")
        return None

    def check_recent_cache(self, shop_id: str, platform: str = "", user_id: str = "", max_seconds: int = 60) -> Optional[Dict[str, Any]]:
        """
        檢查 1 分鐘 (60 秒) 內是否已有該店鋪的比對報告，若有則直接回傳快取結果
        """
        sql = """
            SELECT * FROM compare_reports 
            WHERE shop_id = ? 
            ORDER BY created_at DESC LIMIT 1;
        """
        rows = self.db.execute(sql, [shop_id])
        if not rows:
            return None

        latest_rpt = rows[0]
        created_str = latest_rpt.get("created_at")
        if not created_str:
            return None

        try:
            rpt_time = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIWAN_TZ)
            diff_seconds = (get_tw_now() - rpt_time).total_seconds()
            if 0 <= diff_seconds <= max_seconds:
                p_name = latest_rpt.get("platform") or platform or "店鋪"
                print(f"⚡ [命中 1 分鐘快取] 距上次比對僅 {int(diff_seconds)} 秒，直接回傳報告 {latest_rpt['report_id']}")
                return latest_rpt
        except Exception as e:
            print(f"[Warn] 快取時間解析失敗: {e}")

        return None

    def match_house(self, h: Dict[str, Any], yc_list: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str, str, float]:
        """
        通用兩階段嚴格比對核心：
        階段一 (Hard Filters 硬性門檻)：
          1. 縣市、行政區嚴格完全相同 (不同即判定為不同物件)
          2. 所在樓層嚴格完全相同 (非透天/整棟需一致；透天整棟相容 99、1、0、-1 地下車庫別墅)
          3. 建物登記坪數容許誤差 <= 1.5 坪
          4. 社區名稱若吻合，總樓高放寬至 +-3 樓 (因頂樓屋突/地下室計算常有出入)；無社區吻合則限制 +-1 樓
          5. 屋齡比對：若社區吻合不硬性排除 (留待第二階段提示填寫有誤)；無社區吻合則容許誤差 <= 3.0 年
          6. 道路經緯度距離定位檢核：若無社區吻合且兩條路相距 > 800 公尺 -> 判定不同物件

        階段二 (Difference Diagnostics 差異診斷)：
          - 總價差 >= 1 萬標註價差
          - 總樓高出入標註樓高差
          - 屋齡出入標註屋齡差 (例如標 108年、113年 或差距 > 2.5 年)
          - 路名出入標註路名差
          - 若縣市/區域/建坪/樓層/總價/總高皆一致，僅路名不同 -> 特別提示：是否是路名有誤 (極少機會 但也有可能)
        
        回傳: (best_yc_match, status, reason, confidence_score)
        """
        platform_name = h.get("platform") or "平台"
        c1 = (h.get("county") or "").strip()
        d1 = (h.get("district") or "").strip()
        f1 = int(h.get("floor") or 0)
        a1 = float(h.get("reg_area") or 0.0)
        p1 = float(h.get("price") or 0.0)
        allf1 = int(h.get("all_floor") or 0)
        raw_age1 = float(h.get("house_age") or 0.0)
        current_roc_year = datetime.now().year - 1911
        # 若屋齡 >= 60，視為民國完工年份 (如 108年、113年)，自動換算為實際屋齡
        age1 = max(0.0, round(current_roc_year - raw_age1, 1)) if raw_age1 >= 60.0 else raw_age1
        st1 = (h.get("street") or "").strip()
        comm1 = clean_text(h.get("community_name") or "")
        t1 = clean_text(h.get("title") or "")

        # 1. 第一階段：硬性特徵比對
        candidates = []
        for y in yc_list:
            c2 = (y.get("county") or "").strip()
            d2 = (y.get("district") or "").strip()
            if c1 != c2 or d1 != d2:
                continue

            f2 = int(y.get("from_floor") or 0)
            to_f2 = int(y.get("to_floor") or 0)
            up_f2 = int(y.get("up_floor") or 0)

            # 樓層比對 (大樓公寓嚴格一致；透天整棟相容 99、1、-1 地下車庫別墅、0 或 to_floor == up_floor)
            if f1 > 0 and f1 != 99:
                if f1 != f2:
                    continue
            elif f1 == 99:
                if f2 not in [-1, 0, 1, 99] and to_f2 != up_f2:
                    continue

            # 建物登記坪數 (容許誤差 <= 1.5 坪)
            a2 = float(y.get("reg_area") or 0.0)
            diff_area = abs(a1 - a2)
            if diff_area > 1.5:
                continue

            # 社區名稱吻合度檢查
            comm2 = clean_text(y.get("community_name") or "")
            case2 = clean_text(y.get("case_name") or "")
            addr2 = (y.get("address") or "").strip()
            comm_match = False
            if comm1 and comm2 and (comm1 in comm2 or comm2 in comm1):
                comm_match = True
            elif comm2 and comm2 in t1:
                comm_match = True

            # 總樓高比對：若社區吻合則不在此階段硬排除 (留至第二階段提示，避免因地下室或頂樓計算出入被誤判下架)
            # 若無社區吻合，總樓高容許誤差 <= 1 樓
            if not comm_match and allf1 > 0 and up_f2 > 0:
                if abs(allf1 - up_f2) > 1:
                    continue

            # 屋齡比對：若社區吻合不在此階段硬性排除 (避免人員打錯如 108、113 年被誤判下架，留待第二階段提示)
            # 若無社區吻合，雙方皆有屋齡時，容許誤差 <= 3.0 年
            raw_age2 = float(y.get("build_age") or 0.0)
            age2 = max(0.0, round(current_roc_year - raw_age2, 1)) if raw_age2 >= 60.0 else raw_age2
            if not comm_match and age1 > 0 and age2 > 0:
                if abs(age1 - age2) > 3.0:
                    continue

            # 道路經緯度距離定位檢核：若無社區吻合，且兩條路實際相距超過 800 公尺 -> 判定不同物件！
            if not comm_match and is_road_too_far(d1, st1, addr2, max_meters=800.0):
                continue

            candidates.append((diff_area, y, comm_match))

        if not candidates:
            return None, "NOT_FOUND", "🔴 分店目前無此庫存物件\n• 疑似已成交下架，或未在 11 家加盟店刊登", 0.0

        # 若有多筆符合硬性條件，以 社區/路名吻合度 與 坪數誤差 排序
        def score_cand(item):
            diff_a, y, c_match = item
            score = 50.0  # 通過硬性篩選的基本分
            if c_match:
                score += 35.0

            case2 = clean_text(y.get("case_name") or "")
            addr2 = (y.get("address") or "").strip()
            st1_norm = st1.replace('臺', '台')
            if st1 and (st1_norm in addr2.replace('臺', '台') or st1_norm in case2):
                score += 20.0

            score += max(0.0, 10.0 - diff_a * 5)
            return score

        candidates.sort(key=score_cand, reverse=True)
        best_diff_a, best_y, _ = candidates[0]
        confidence = min(100.0, score_cand((best_diff_a, best_y, _)))

        # 2. 第二階段：細節差異診斷
        anomalies = []
        p2 = float(best_y.get("price") or 0.0)
        allf2 = int(best_y.get("up_floor") or 0)
        raw_age2 = float(best_y.get("build_age") or 0.0)
        age2 = max(0.0, round(current_roc_year - raw_age2, 1)) if raw_age2 >= 60.0 else raw_age2
        addr2 = (best_y.get("address") or "").strip()
        case2 = (best_y.get("case_name") or "").strip()

        # 總價判斷
        diff_price = round(p1 - p2, 2)
        has_price_diff = abs(diff_price) >= 1.0
        if has_price_diff:
            diff_label = f"永慶便宜 {abs(diff_price)}萬" if diff_price > 0 else f"{platform_name}便宜 {abs(diff_price)}萬"
            anomalies.append(f"總價不一致：{platform_name} 標 {p1}萬 vs 永慶標 {p2}萬 ({diff_label})")

        # 總樓高判斷
        has_allf_diff = False
        if allf1 > 0 and allf2 > 0 and allf1 != allf2:
            has_allf_diff = True
            anomalies.append(f"總樓高出入：{platform_name} 標 {allf1}樓高 vs 永慶標 {allf2}樓高")

        # 屋齡判斷 (若差異大於 2.5 年，進行出入提示)
        has_age_diff = False
        if age1 > 0 and age2 > 0 and abs(age1 - age2) > 2.5:
            has_age_diff = True
            anomalies.append(f"屋齡出入：{platform_name} 標 {age1}年 vs 永慶標 {age2}年 (疑似屋齡填寫有誤)")

        # 路名判斷 (統一 臺/台)
        has_street_diff = False
        st1_norm = st1.replace('臺', '台')
        addr2_norm = addr2.replace('臺', '台')
        case2_norm = case2.replace('臺', '台')
        if st1 and (st1_norm not in addr2_norm and st1_norm not in case2_norm):
            has_street_diff = True
            anomalies.append(f"路名出入：{platform_name} 標「{st1}」 vs 永慶地址「{addr2}」")

        # 特殊判斷：縣市區域、建坪、樓層、總價、總樓高、屋齡都一樣，只有路名不同
        if not has_price_diff and not has_allf_diff and not has_age_diff and has_street_diff:
            status = "SPEC_MISMATCH"
            reason = (
                f"💡 提示：是否是路名有誤 (極少機會 但也有可能)\n"
                f"• 縣市/區域/樓層/建坪/總價/總高皆一致\n"
                f"• {platform_name} 路名:「{st1}」\n"
                f"• 永慶地址:「{addr2}」"
            )
            return best_y, status, reason, confidence

        if has_price_diff:
            status = "PRICE_MISMATCH"
            reason = "\n".join([f"• {a}" for a in anomalies])
        elif anomalies:
            status = "SPEC_MISMATCH"
            reason = "\n".join([f"• {a}" for a in anomalies])
        else:
            status = "NORMAL"
            f_disp = f"{f1}樓" if f1 != 99 else "整棟"
            age_disp = f"{age1}年" if age1 > 0 else "未載明"
            reason = (
                f"🟢 正常在售：縣市與區域完全吻合\n"
                f"• 總價一致 ({p1}萬)\n"
                f"• 樓層一致 ({f_disp})\n"
                f"• 建坪相符 ({a1}坪)\n"
                f"• 屋齡相符 ({age_disp})\n"
                f"• 總樓高與路名皆相符"
            )

        return best_y, status, reason, confidence

    def run_comparison(
        self,
        platform: str = "591",
        shop_id: str = "",
        houses: Optional[List[Dict[str, Any]]] = None,
        shop_url: str = "",
        user_id: str = ""
    ) -> str:
        """
        執行跨平台通用比對作業並持久化報告
        :param platform: 平台名稱 ('591', '5168', 'rakuya')
        :param shop_id: 店鋪代號或手機
        :param houses: 標準化物件列表 (若無則自動從資料庫讀取)
        :param shop_url: 店鋪完整 URL
        :param user_id: LINE 使用者 ID
        :return: report_id
        """
        # 1. 檢查 1 分鐘內快取
        cached = self.check_recent_cache(shop_id, platform=platform, user_id=user_id, max_seconds=60)
        if cached:
            return cached["report_id"]

        report_id = f"rpt_{platform}_{shop_id}_{get_tw_now().strftime('%Y%m%d_%H%M%S')}"
        now_str = get_tw_now_str()
        print(f"\n================ 開始比對 {platform} 店鋪 {shop_id} 庫存 ================")

        # 2. 取得待比對的物件列表
        std_houses = houses
        if not std_houses:
            if platform == "5168":
                db_rows = self.db.execute("SELECT * FROM t5168_houses WHERE account = ?;", [shop_id])
                if not db_rows:
                    raise ValueError(f"5168 店鋪 {shop_id} 在資料庫中無物件快照。")
                std_houses = []
                for r in db_rows:
                    std_houses.append({
                        "platform": "5168",
                        "house_id": r.get("house_id"),
                        "title": r.get("title"),
                        "url": r.get("url"),
                        "county": r.get("county"),
                        "district": r.get("district"),
                        "street": r.get("street"),
                        "community_name": r.get("community_name"),
                        "price": float(r.get("price") or 0.0),
                        "reg_area": float(r.get("reg_area") or 0.0),
                        "main_area": float(r.get("main_area") or 0.0) if r.get("main_area") is not None else None,
                        "floor": int(r.get("floor") or 0),
                        "all_floor": int(r.get("all_floor") or 0),
                        "house_age": float(r.get("house_age") or 0.0),
                        "room": int(r.get("room") or 0),
                        "case_type": r.get("case_type"),
                        "cover": r.get("cover")
                    })
            else:
                db_rows = self.db.execute("SELECT * FROM t591_houses WHERE shop_id = ?;", [shop_id])
                if not db_rows:
                    raise ValueError(f"591 店鋪 {shop_id} 在資料庫中無物件快照。")
                std_houses = []
                for r in db_rows:
                    std_houses.append({
                        "platform": "591",
                        "house_id": r.get("house_id"),
                        "title": r.get("title"),
                        "url": f"https://sale.591.com.tw/sale-detail-{r.get('house_id')}.html",
                        "county": r.get("county"),
                        "district": r.get("district"),
                        "street": r.get("street"),
                        "community_name": r.get("community_name"),
                        "price": float(r.get("price") or 0.0),
                        "reg_area": float(r.get("reg_area") or 0.0),
                        "main_area": float(r.get("main_area") or 0.0) if r.get("main_area") is not None else None,
                        "floor": int(r.get("floor") or 0),
                        "all_floor": int(r.get("all_floor") or 0),
                        "house_age": float(r.get("house_age") or 0.0),
                        "room": int(r.get("room") or 0),
                        "case_type": r.get("kind_name"),
                        "cover": r.get("cover")
                    })

        # 3. 取得永慶所有分店在庫物件
        yc_list = self.db.execute(
            """
            SELECT y.*, b.name as branch_name 
            FROM yungching_houses y
            LEFT JOIN branches b ON y.branch_id = b.branch_id;
            """
        )
        yc_inventory_count = len(yc_list)
        yc_synced_at = max((y.get("updated_at") or "") for y in yc_list) if yc_list else ""

        print(f"{platform} 待比對: {len(std_houses)} 筆 | 永慶分店在庫: {yc_inventory_count} 筆 (最新同步: {yc_synced_at})")

        matched_count = 0
        price_diff_count = 0
        not_found_count = 0
        spec_diff_count = 0
        report_items = []

        for h in std_houses:
            p_val = float(h.get("price") or 0.0)
            hid = str(h.get("house_id"))
            title = h.get("title") or ""
            area_val = float(h.get("reg_area") or 0.0)
            f_val = h.get("floor")
            allf_val = h.get("all_floor")
            f_str = f"{f_val}/{allf_val}" if f_val != 99 else f"整棟/{allf_val}"
            comm_val = h.get("community_name") or ""
            cover_val = h.get("cover") or ""
            h_url = h.get("url") or ""
            addr_val = f"{h.get('county') or ''}{h.get('district') or ''}{h.get('street') or ''}".strip()

            best_match, status, reason, confidence = self.match_house(h, yc_list)

            if status == "NORMAL":
                matched_count += 1
            elif status == "PRICE_MISMATCH":
                price_diff_count += 1
            elif status == "SPEC_MISMATCH":
                spec_diff_count += 1
            else:
                not_found_count += 1

            if best_match:
                pyc = float(best_match.get("price") or 0.0)
                sid = best_match.get("case_sid")
                b_name = best_match.get("branch_name") or best_match.get("branch_id")
                b_url = f"https://buy.yungching.com.tw/house/{sid}"

                report_items.append((
                    report_id, shop_id, hid, title, p_val, area_val, f_str, comm_val, cover_val, h_url,
                    sid, best_match.get("branch_id"), b_name, pyc, b_url,
                    status, reason, confidence, now_str, platform, addr_val
                ))
            else:
                report_items.append((
                    report_id, shop_id, hid, title, p_val, area_val, f_str, comm_val, cover_val, h_url,
                    None, None, None, None, None,
                    status, reason, confidence, now_str, platform, addr_val
                ))

        # 3.5 清理此店鋪先前的歷史比對報告 (確保資料庫只保留最新報告，避免累積重複歷史紀錄)
        try:
            self.db.execute(
                """
                DELETE FROM compare_items 
                WHERE report_id IN (SELECT report_id FROM compare_reports WHERE shop_id = ? AND platform = ?);
                """,
                [shop_id, platform]
            )
            self.db.execute(
                """
                DELETE FROM compare_reports 
                WHERE shop_id = ? AND platform = ?;
                """,
                [shop_id, platform]
            )
            print(f"🧹 已清理 {platform} 店鋪 {shop_id} 既有歷史比對記錄。")
        except Exception as e:
            print(f"[Warn] 清理歷史報告失敗: {e}")

        # 4. 儲存報告主表 (包含永慶分店在庫統計與同步時間)
        full_shop_url = shop_url
        if not full_shop_url:
            if platform == "5168":
                full_shop_url = f"https://realtor.houseprice.tw/agent/buy/{shop_id}/"
            elif platform == "rakuya":
                full_shop_url = f"https://vip.rakuya.com.tw/{shop_id}/sell"
            else:
                full_shop_url = f"https://www.591.com.tw/broker{shop_id}"

        self.db.execute(
            """
            INSERT INTO compare_reports (
                report_id, shop_id, shop_url, user_id, total_591_count, matched_count,
                price_diff_count, not_found_count, spec_diff_count, created_at, platform,
                yc_inventory_count, yc_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [
                report_id, shop_id, full_shop_url, user_id, len(std_houses), matched_count,
                price_diff_count, not_found_count, spec_diff_count, now_str, platform,
                yc_inventory_count, yc_synced_at
            ]
        )

        # 5. 批次儲存明細表 (含 t591_address)
        insert_item_sql = """
            INSERT INTO compare_items (
                report_id, shop_id, t591_id, t591_title, t591_price, t591_area, t591_floor,
                t591_community, t591_cover, t591_url, matched_case_sid, matched_branch_id,
                matched_branch_name, matched_price, matched_url, status, diff_reason,
                confidence_score, created_at, platform, t591_address
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        batch_items = [(insert_item_sql, list(item)) for item in report_items]
        chunk_size = 50
        for i in range(0, len(batch_items), chunk_size):
            self.db.batch(batch_items[i:i+chunk_size])

        abnormal_total = price_diff_count + not_found_count + spec_diff_count
        print(f"\n================ {platform} 比對完成統計報告 ================")
        print(f"報告 ID: {report_id}")
        print(f"{platform} 店鋪總庫存: {len(std_houses)} 筆")
        print(f"  🟢 正常在售: {matched_count} 筆")
        print(f"  🔴 查無分店庫存 (標註下架): {not_found_count} 筆")
        print(f"  🟡 價格調整/不一致: {price_diff_count} 筆")
        print(f"  🟠 規格出入 (樓層/總高/屋齡/路名): {spec_diff_count} 筆")
        print(f"  總異常件數: {abnormal_total} 筆")

        return report_id

if __name__ == "__main__":
    comparator = InventoryComparator()
    # 測試 5168
    comparator.run_comparison("5168", "0968682023")
