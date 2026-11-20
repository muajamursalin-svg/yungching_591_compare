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

    def calculate_match_score(self, h591: Dict[str, Any], hyc: Dict[str, Any]) -> Tuple[float, List[str]]:
        """
        綜合評估：縣市、區域、路名、社區、總價、樓層、總樓高、屋齡
        回傳 (匹配分數, 異常原因列表)
        門檻原則：路名或社區至少需有一個相符，且縣市區域必須一致，否則視為不同物件。
        """
        score = 0.0
        anomalies = []

        # 1. 縣市與行政區 (必要條件)
        c591 = (h591.get("county") or "").strip()
        d591 = (h591.get("district") or "").strip()
        cyc = (hyc.get("county") or "").strip()
        dyc = (hyc.get("district") or "").strip()

        if c591 and cyc and c591 != cyc:
            return 0.0, ["縣市不相符"]
        if d591 and dyc and d591 != dyc:
            return 0.0, ["區域不相符"]

        score += 20.0

        # 2. 社區名稱與路名比對 (關鍵辨識：二者至少有一項相符)
        comm591 = (h591.get("community_name") or "").strip()
        commyc = (hyc.get("community_name") or "").strip()
        t591 = (h591.get("title") or "").strip()

        st591 = (h591.get("street") or "").strip()
        addryc = (hyc.get("address") or "").strip()
        case_name_yc = (hyc.get("case_name") or "").strip()

        # 社區是否吻合
        comm_match = False
        if comm591 and commyc and (comm591 == commyc or comm591 in commyc or commyc in comm591):
            comm_match = True
        elif commyc and commyc in t591:
            comm_match = True

        # 路名是否吻合
        st_match = False
        if st591 and (st591 in addryc or st591 in case_name_yc):
            st_match = True

        # 若路名與社區完全對不上，絕對不可能是同一間房子
        if not comm_match and not st_match:
            return 0.0, ["路名與社區皆不相符"]

        if comm_match:
            score += 35.0
        if st_match:
            score += 20.0

        # 3. 坪數比對
        area591 = float(h591.get("reg_area") or 0.0)
        areayc = float(hyc.get("reg_area") or 0.0)
        diff_area = abs(area591 - areayc)

        if diff_area <= 0.3:
            score += 25.0
        elif diff_area <= 1.0:
            score += 15.0
        elif diff_area > 3.0:
            score -= 20.0
            anomalies.append(f"坪數差距過大 (591: {area591}坪 vs 永慶: {areayc}坪，差 {round(diff_area, 1)}坪)")

        # 4. 總價比對 (不扣分，但列為異常指標)
        p591 = float(h591.get("price") or 0.0)
        pyc = float(hyc.get("price") or 0.0)
        diff_price = round(p591 - pyc, 2)
        if abs(diff_price) >= 1.0:
            diff_label = f"永慶便宜 {abs(diff_price)}萬" if diff_price > 0 else f"591便宜 {abs(diff_price)}萬"
            anomalies.append(f"總價不一致 (591標: {p591}萬 vs 永慶標: {pyc}萬，{diff_label})")

        # 5. 樓層比對
        f591 = int(h591.get("floor") or 0)
        f_from_yc = int(hyc.get("from_floor") or 0)
        if f591 != 99 and f591 > 0 and f_from_yc > 0:
            if f591 == f_from_yc:
                score += 10.0
            else:
                anomalies.append(f"所在樓層出入 (591標: {f591}樓 vs 永慶標: {f_from_yc}樓)")

        # 6. 總樓高比對
        allf591 = int(h591.get("all_floor") or 0)
        up_yc = int(hyc.get("up_floor") or 0)
        if allf591 > 0 and up_yc > 0:
            if allf591 == up_yc:
                score += 10.0
            else:
                anomalies.append(f"總樓高出入 (591標: {allf591}樓高 vs 永慶標: {up_yc}樓高)")

        # 7. 屋齡比對
        age591 = float(h591.get("house_age") or 0.0)
        ageyc = float(hyc.get("build_age") or 0.0)
        if age591 > 0 and ageyc > 0:
            diff_age = abs(age591 - ageyc)
            if diff_age <= 1.5:
                score += 10.0
            elif diff_age > 3.0:
                anomalies.append(f"屋齡差距過大 (591: {age591}年 vs 永慶: {round(ageyc, 1)}年，差 {round(diff_age, 1)}年)")

        final_score = max(0.0, min(100.0, score))
        return final_score, anomalies

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
            county = h.get("county") or ""
            district = h.get("district") or ""
            p591 = float(h.get("price") or 0.0)
            hid = h.get("house_id")
            title = h.get("title") or ""
            area591 = float(h.get("reg_area") or 0.0)
            f591_str = f"{h.get('floor')}/{h.get('all_floor')}" if h.get("floor") != 99 else f"整棟/{h.get('all_floor')}"
            comm591 = h.get("community_name") or ""
            cover591 = h.get("cover") or ""
            url591 = f"https://sale.591.com.tw/sale-detail-{hid}.html"

            # 縮小候選比對範圍
            candidates = [
                y for y in yc_list 
                if (county in y.get("county", "") or not county) and (district in y.get("district", "") or not district)
            ]
            if not candidates:
                candidates = yc_list

            best_match = None
            best_score = 0.0
            best_anomalies = []

            for cand in candidates:
                score, anomalies = self.calculate_match_score(h, cand)
                if score > best_score:
                    best_score = score
                    best_match = cand
                    best_anomalies = anomalies

            # 判定匹配門檻 (60 分以上且路名/社區吻合視為同一物件)
            if best_match and best_score >= 60.0:
                pyc = float(best_match.get("price") or 0.0)
                sid = best_match.get("case_sid")
                b_name = best_match.get("branch_name") or best_match.get("branch_id")
                b_url = f"https://buy.yungching.com.tw/house/{sid}"

                if best_anomalies:
                    # 存在一項或多項差距過大
                    has_price_diff = any("總價不一致" in a for a in best_anomalies)
                    if has_price_diff:
                        status = "PRICE_MISMATCH"
                        price_diff_count += 1
                    else:
                        status = "SPEC_MISMATCH"
                        spec_diff_count += 1

                    reason = "；".join(best_anomalies)
                else:
                    status = "NORMAL"
                    matched_count += 1
                    reason = f"正常在售：縣市/區域/路名相符，總價({p591}萬)、樓層、總高、屋齡皆一致"

                report_items.append((
                    report_id, shop_id, hid, title, p591, area591, f591_str, comm591, cover591, url591,
                    sid, best_match.get("branch_id"), b_name, pyc, b_url,
                    status, reason, best_score, now_str
                ))
            else:
                # 查無相符物件 -> 標註下架
                status = "NOT_FOUND"
                not_found_count += 1
                reason = "分店目前無此庫存物件 (疑似已成交下架或未在分店刊登)"

                report_items.append((
                    report_id, shop_id, hid, title, p591, area591, f591_str, comm591, cover591, url591,
                    None, None, None, None, None,
                    status, reason, best_score, now_str
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
    comparator.run_comparison("12017")
