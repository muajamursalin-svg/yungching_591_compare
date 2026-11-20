import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from turso_client import TursoClient

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
    # 移除特殊標點符號與多餘空白
    s = re.sub(r'[\s\-_\/|~#🌸❤️⭐️✨【】\(\)（）\.\,，、]', '', s)
    return s.strip()

class InventoryComparator:
    def __init__(self, db_client: Optional[TursoClient] = None):
        self.db = db_client or TursoClient()

    def check_recent_cache(self, shop_id: str, user_id: str = "", max_seconds: int = 60) -> Optional[Dict[str, Any]]:
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
                print(f"⚡ [命中 1 分鐘快取] 距上次比對僅 {int(diff_seconds)} 秒，直接回傳報告 {latest_rpt['report_id']}")
                return latest_rpt
        except Exception as e:
            print(f"[Warn] 快取時間解析失敗: {e}")

        return None

    def match_house(self, h: Dict[str, Any], yc_list: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str, str, float]:
        """
        兩階段嚴格比對：
        階段一 (Hard Filters)：
          - 縣市、行政區嚴格完全相同 (不同就判斷不同物件)
          - 所在樓層嚴格完全相同 (非透天/整棟需一致；透天整棟相容)
          - 建物登記坪數容許誤差 <= 1.5 坪
        階段二 (Difference Diagnostics)：
          - 總價差 >= 1 萬標註價差
          - 總樓高出入標註樓高差
          - 路名出入標註路名差
          - 若縣市/區域/建坪/樓層/總價/總樓高皆一致，僅路名不同 -> 特別提示：是否是路名有誤 (極少機會 但也有可能)
        回傳: (best_yc_match, status, reason, confidence_score)
        """
        c1 = (h.get("county") or "").strip()
        d1 = (h.get("district") or "").strip()
        f1 = int(h.get("floor") or 0)
        a1 = float(h.get("reg_area") or 0.0)
        p1 = float(h.get("price") or 0.0)
        allf1 = int(h.get("all_floor") or 0)
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
            # 樓層比對 (大樓/公寓嚴格一致；透天/整棟相容 99 與 1)
            if f1 > 0 and f2 > 0 and f1 != 99:
                if f1 != f2:
                    continue
            elif f1 == 99 and f2 != 99 and f2 != 1:
                continue

            a2 = float(y.get("reg_area") or 0.0)
            diff_area = abs(a1 - a2)
            if diff_area > 1.5:
                continue

            candidates.append((diff_area, y))

        if not candidates:
            return None, "NOT_FOUND", "分店目前無此庫存物件 (疑似已成交下架或未在分店刊登)", 0.0

        # 若有多筆符合硬性條件，以 社區/路名吻合度 與 坪數誤差 排序
        def score_cand(item):
            diff_a, y = item
            score = 50.0  # 通過硬性篩選的基本分
            comm2 = clean_text(y.get("community_name") or "")
            case2 = clean_text(y.get("case_name") or "")
            addr2 = (y.get("address") or "").strip()

            if comm1 and comm2 and (comm1 in comm2 or comm2 in comm1):
                score += 30.0
            elif comm2 and comm2 in t1:
                score += 25.0

            if st1 and (st1 in addr2 or st1 in case2):
                score += 20.0

            score += max(0.0, 10.0 - diff_a * 5)
            return score

        candidates.sort(key=score_cand, reverse=True)
        best_diff_a, best_y = candidates[0]
        confidence = min(100.0, score_cand((best_diff_a, best_y)))

        # 2. 第二階段：細節差異診斷
        anomalies = []
        p2 = float(best_y.get("price") or 0.0)
        allf2 = int(best_y.get("up_floor") or 0)
        addr2 = (best_y.get("address") or "").strip()
        case2 = (best_y.get("case_name") or "").strip()

        # 總價判斷
        diff_price = round(p1 - p2, 2)
        has_price_diff = abs(diff_price) >= 1.0
        if has_price_diff:
            diff_label = f"永慶便宜 {abs(diff_price)}萬" if diff_price > 0 else f"591便宜 {abs(diff_price)}萬"
            anomalies.append(f"總價不一致 (591標: {p1}萬 vs 永慶標: {p2}萬，{diff_label})")

        # 總樓高判斷
        has_allf_diff = False
        if allf1 > 0 and allf2 > 0 and allf1 != allf2:
            has_allf_diff = True
            anomalies.append(f"總樓高出入 (591標: {allf1}樓高 vs 永慶標: {allf2}樓高)")

        # 路名判斷
        has_street_diff = False
        if st1 and (st1 not in addr2 and st1 not in case2):
            has_street_diff = True
            anomalies.append(f"路名出入 (591:「{st1}」 vs 永慶地址:「{addr2}」)")

        # 特殊判斷：縣市區域、建坪、樓層、總價、總樓高都一樣，只有路名不同
        if not has_price_diff and not has_allf_diff and has_street_diff:
            status = "SPEC_MISMATCH"
            reason = f"💡 提示：是否是路名有誤 (極少機會 但也有可能)（縣市/區域/樓層/建坪/總價({p1}萬)/總樓高皆一致，僅路名 591「{st1}」vs 永慶「{addr2}」不同）"
            return best_y, status, reason, confidence

        if has_price_diff:
            status = "PRICE_MISMATCH"
            reason = "；".join(anomalies)
        elif anomalies:
            status = "SPEC_MISMATCH"
            reason = "；".join(anomalies)
        else:
            status = "NORMAL"
            f_disp = f"{f1}樓" if f1 != 99 else "整棟"
            reason = f"正常在售：縣市/區域/路名相符，總價({p1}萬)、樓層({f_disp})、建坪({a1}坪)、總高皆一致"

        return best_y, status, reason, confidence

    def run_comparison(self, shop_id: str, shop_url: str = "", user_id: str = "") -> str:
        # 1. 檢查 1 分鐘內快取
        cached = self.check_recent_cache(shop_id, user_id=user_id, max_seconds=60)
        if cached:
            return cached["report_id"]

        report_id = f"rpt_{shop_id}_{get_tw_now().strftime('%Y%m%d_%H%M%S')}"
        now_str = get_tw_now_str()
        print(f"\n================ 開始比對 591 店鋪 {shop_id} 庫存 ================")

        # 取得 591 物件清單
        h591_list = self.db.execute("SELECT * FROM t591_houses WHERE shop_id = ?;", [shop_id])
        if not h591_list:
            raise ValueError(f"店鋪 {shop_id} 在資料庫中無物件快照。")

        # 取得永慶所有分店在庫物件
        yc_list = self.db.execute(
            """
            SELECT y.*, b.name as branch_name 
            FROM yungching_houses y
            LEFT JOIN branches b ON y.branch_id = b.branch_id;
            """
        )
        print(f"591 待比對: {len(h591_list)} 筆 | 永慶分店在庫: {len(yc_list)} 筆")

        matched_count = 0
        price_diff_count = 0
        not_found_count = 0
        spec_diff_count = 0
        report_items = []

        for h in h591_list:
            p591 = float(h.get("price") or 0.0)
            hid = h.get("house_id")
            title = h.get("title") or ""
            area591 = float(h.get("reg_area") or 0.0)
            f591_str = f"{h.get('floor')}/{h.get('all_floor')}" if h.get("floor") != 99 else f"整棟/{h.get('all_floor')}"
            comm591 = h.get("community_name") or ""
            cover591 = h.get("cover") or ""
            url591 = f"https://sale.591.com.tw/sale-detail-{hid}.html"

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
                    report_id, shop_id, hid, title, p591, area591, f591_str, comm591, cover591, url591,
                    sid, best_match.get("branch_id"), b_name, pyc, b_url,
                    status, reason, confidence, now_str
                ))
            else:
                report_items.append((
                    report_id, shop_id, hid, title, p591, area591, f591_str, comm591, cover591, url591,
                    None, None, None, None, None,
                    status, reason, confidence, now_str
                ))

        # 儲存報告主表 (記錄 shop_url 與 user_id)
        full_shop_url = shop_url or f"https://www.591.com.tw/broker{shop_id}"
        self.db.execute(
            """
            INSERT INTO compare_reports (
                report_id, shop_id, shop_url, user_id, total_591_count, matched_count,
                price_diff_count, not_found_count, spec_diff_count, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [
                report_id, shop_id, full_shop_url, user_id, len(h591_list), matched_count,
                price_diff_count, not_found_count, spec_diff_count, now_str
            ]
        )

        # 批次儲存明細表
        insert_item_sql = """
            INSERT INTO compare_items (
                report_id, shop_id, t591_id, t591_title, t591_price, t591_area, t591_floor,
                t591_community, t591_cover, t591_url, matched_case_sid, matched_branch_id,
                matched_branch_name, matched_price, matched_url, status, diff_reason,
                confidence_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        batch_items = [(insert_item_sql, list(item)) for item in report_items]
        chunk_size = 50
        for i in range(0, len(batch_items), chunk_size):
            self.db.batch(batch_items[i:i+chunk_size])

        abnormal_total = price_diff_count + not_found_count + spec_diff_count
        print("\n================ 比對完成統計報告 ================")
        print(f"報告 ID: {report_id}")
        print(f"591 店鋪總庫存: {len(h591_list)} 筆")
        print(f"  🟢 正常在售: {matched_count} 筆")
        print(f"  🔴 查無分店庫存 (標註下架): {not_found_count} 筆")
        print(f"  🟡 價格調整/不一致: {price_diff_count} 筆")
        print(f"  🟠 規格出入 (樓層/總高/屋齡/路名): {spec_diff_count} 筆")
        print(f"  總異常件數: {abnormal_total} 筆")

        return report_id

if __name__ == "__main__":
    comparator = InventoryComparator()
    comparator.run_comparison("32192")
