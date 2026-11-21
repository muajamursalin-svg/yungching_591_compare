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

def extract_section_name(text: str) -> str:
    """
    從地址或路名字串中萃取台灣土地段名 (例如: '青雲段', '丁台三段', '車籠埔段', '高鐵段')
    """
    if not text:
        return ""
    s = text.replace('臺', '台').strip()
    s = re.sub(r'^(.*?([縣市]))?(.*?([區鄉鎮市]))?', '', s)
    m = re.search(r'([\u4e00-\u9fa50-9一二三四五六七八九十]+段)', s)
    if m:
        sec = m.group(1).strip()
        if not re.search(r'[路街道]\w*段$', sec):
            return sec
    return ""

def generate_property_fingerprint(
    county: str,
    district: str,
    street: str,
    floor: Any,
    price: Any,
    reg_area: Any,
    is_land: bool = False
) -> str:
    """
    生成跨平台物業唯一指紋 (供全員共享「內部網頁已上架」比對使用)
    若為土地：{county}_{district}_{section}_land_{price}M_{area}P
    若為房屋：{county}_{district}_{street}_{floor}F_{price}M_{area}P
    """
    c = clean_text(str(county or "")).replace('臺', '台')
    d = clean_text(str(district or "")).replace('臺', '台')
    s = clean_text(str(street or "")).replace('臺', '台')

    try:
        p = f"{float(price or 0.0):.1f}M"
    except Exception:
        p = "0.0M"

    try:
        a = f"{float(reg_area or 0.0):.1f}P"
    except Exception:
        a = "0.0P"

    if is_land or s.endswith("段") or bool(extract_section_name(s)):
        sec = extract_section_name(s) or s
        return f"{c}_{d}_{sec}_land_{p}_{a}".lower()

    try:
        f = int(floor or 0)
    except Exception:
        f = 0
    f_code = "99F" if f in [0, 99, -1] else f"{f}F"

    return f"{c}_{d}_{s}_{f_code}_{p}_{a}".lower()


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

    def match_house(
        self,
        h: Dict[str, Any],
        yc_list: List[Dict[str, Any]],
        internal_list: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[Optional[Dict[str, Any]], str, str, float]:
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
        age1 = float(h.get("house_age") or 0.0)
        st1 = (h.get("street") or "").strip()
        comm1 = clean_text(h.get("community_name") or "")
        t1 = clean_text(h.get("title") or "")

        # 0. 判斷是否為「土地類型」物件 (純依底層 API 官方欄位 is_land 或 case_type == '土地'，絕不看標題行銷詞)
        is_h_land = bool(h.get("is_land")) or (h.get("case_type") == "土地")

        if is_h_land:
            h_sec = extract_section_name(st1) or extract_section_name(t1)
            h_land_area = float(h.get("land_area") or h.get("reg_area") or a1 or 0.0)

            land_candidates = []
            for y in yc_list:
                # 判斷永慶分店在庫物件是否為土地 (純依官方欄位 case_type 或 purpose)
                is_y_land = (y.get("case_type") == "土地" or y.get("purpose") == "土地")
                if not is_y_land:
                    continue

                # 縣市比對
                c1_norm = c1.replace('臺', '台')
                c2_norm = (y.get("county") or "").strip().replace('臺', '台')
                if c1_norm != c2_norm:
                    continue

                # 行政區比對
                d2 = (y.get("district") or "").strip()
                if d1 and d2 and d1 != d2:
                    continue

                # 段名比對 (核心：若雙方皆有段名，段名必須相同)
                y_addr = (y.get("address") or "").strip()
                y_case = (y.get("case_name") or "").strip()
                y_sec = extract_section_name(y_addr) or extract_section_name(y_case)

                if h_sec and y_sec and (h_sec != y_sec):
                    continue
                elif h_sec and not y_sec:
                    if h_sec not in y_addr and h_sec not in y_case:
                        continue
                elif not h_sec and y_sec:
                    if y_sec not in st1 and y_sec not in t1:
                        continue

                # 地坪比對 (容許誤差 <= 2.0 坪)
                y_land_area = float(y.get("land_area") or y.get("reg_area") or 0.0)
                diff_area = abs(h_land_area - y_land_area) if (h_land_area > 0 and y_land_area > 0) else 0.0
                if h_land_area > 0 and y_land_area > 0 and diff_area > 2.0:
                    continue

                land_candidates.append((diff_area, y, y_sec))

            if not land_candidates:
                # 檢查是否有同仁手動回報之「內部網頁已上架」土地紀錄
                if internal_list:
                    h_fp = generate_property_fingerprint(c1, d1, h_sec or st1, 0, p1, h_land_area, is_land=True)
                    for item in internal_list:
                        matched_int = False
                        anomaly_msg = ""
                        header_str = "🟢 內部網頁已上架 (同仁手動回報・全員共享)"
                        
                        if item.get("property_fingerprint") == h_fp:
                            matched_int = True
                        else:
                            it_c = clean_text(item.get("county") or "").replace('臺', '台')
                            it_d = clean_text(item.get("district") or "").replace('臺', '台')
                            it_s = clean_text(item.get("street") or "").replace('臺', '台')
                            it_sec = extract_section_name(it_s) or it_s
                            if it_c == c1_norm and (not d1 or it_d == d1):
                                if (h_sec and it_sec and h_sec == it_sec) or (st1 in it_s or it_s in st1):
                                    it_p = float(item.get("price") or 0.0)
                                    it_a = float(item.get("reg_area") or 0.0)
                                    diff_price = abs(p1 - it_p)
                                    diff_area = abs(h_land_area - it_a)

                                    if diff_price <= 50.0 and diff_area <= 2.0:
                                        matched_int = True
                                    elif diff_price <= 50.0 and diff_area > 2.0:
                                        matched_int = True
                                        header_str = "🟢 內部網頁已上架 (地坪出入提示)"
                                        anomaly_msg = f"• 異常警示：⚠️ 外網刊登【地坪 {h_land_area} 坪】與內網【{it_a} 坪】出入\n"
                                    elif diff_area <= 2.0 and diff_price > 50.0:
                                        matched_int = True
                                        header_str = "🟢 內部網頁已上架 (開價出入提示)"
                                        p_delta = round(p1 - it_p, 2)
                                        anomaly_msg = f"• 異常警示：⚠️ 外網開價【{p1} 萬】與內網【{it_p} 萬】不一致 (價差 {p_delta} 萬)\n"

                        if matched_int:
                            rep_time = item.get("created_at") or "近期"
                            rep_user = item.get("reported_by") or "店頭同仁"
                            disp_sec = h_sec or "段名相符"
                            reason = (
                                f"{header_str}\n"
                                f"{anomaly_msg}"
                                f"• 物件類別：土地 ({disp_sec}｜地坪 {h_land_area}坪)\n"
                                f"• 查驗狀態：確認物件存在於I智慧內部 (由 {rep_user} 查核確認)\n"
                                f"• 回報時間：{rep_time}\n"
                                f"• 說明：此土地物件在外網雖查無資料，但經同仁確認已在I智慧內部系統上架中。若後續外網正式同步上架，系統將自動納入在庫並畢業清理。"
                            )
                            pseudo_yc = {
                                "case_sid": item.get("internal_case_no") or item.get("source_house_id"),
                                "branch_id": "INTERNAL",
                                "branch_name": f"內網已在庫 ({disp_sec})",
                                "price": float(item.get("price") or p1),
                                "from_floor": 0,
                                "to_floor": 0,
                                "up_floor": 0,
                                "reg_area": float(item.get("reg_area") or h_land_area),
                                "address": f"{item.get('county') or c1}{item.get('district') or d1}{disp_sec}",
                                "case_name": item.get("case_name") or h.get("title") or "",
                            }
                            return pseudo_yc, "INTERNAL_ACTIVE", reason, 90.0

                return None, "NOT_FOUND", f"🔴 分店目前無此土地庫存物件\n• 查無同段名【{h_sec or '地段'}】與地坪【{h_land_area}坪】之分店在庫紀錄", 0.0

            # 排序候選：地坪誤差最小 > 價差最小
            land_candidates.sort(key=lambda item: (
                item[0],
                abs(p1 - float(item[1].get("price") or 0.0))
            ))
            best_diff_a, best_y, best_y_sec = land_candidates[0]
            p2 = float(best_y.get("price") or 0.0)
            disp_sec = best_y_sec or h_sec or "段名相符"
            y_land_area = float(best_y.get("land_area") or best_y.get("reg_area") or 0.0)

            # 總價判斷 (價差 >= 1.0 萬)
            diff_price = round(p1 - p2, 2)
            if abs(diff_price) >= 1.0:
                diff_label = f"永慶便宜 {abs(diff_price)}萬" if diff_price > 0 else f"{platform_name}便宜 {abs(diff_price)}萬"
                reason = (
                    f"💰 總價標價調整提示：\n"
                    f"• 土地位置：{c1}{d1}【{disp_sec}】\n"
                    f"• 土地坪數：{platform_name} 標 {h_land_area}坪 vs 永慶標 {y_land_area}坪 (地坪相符)\n"
                    f"• 開價出入：{platform_name} 標 {p1}萬 vs 永慶分店標 {p2}萬 ({diff_label})"
                )
                return best_y, "PRICE_MISMATCH", reason, 90.0
            else:
                reason = (
                    f"🟢 正常在售：土地縣市/區域/段名完全吻合\n"
                    f"• 土地位置：{c1}{d1}【{disp_sec}】\n"
                    f"• 地坪相符：{h_land_area}坪\n"
                    f"• 總價一致：{p1}萬\n"
                    f"• 永慶案名：{best_y.get('case_name')} (案號: {best_y.get('case_sid')})"
                )
                return best_y, "NORMAL", reason, 95.0

        # 1. 第一階段：硬性特徵比對 (支援 Pass 1: 嚴格樓層比對 與 Pass 2: 樓層容錯比對)
        def collect_candidates(floor_tolerance: bool = False):
            cand_list = []
            for y in yc_list:
                c1_norm = c1.replace('臺', '台')
                c2_norm = (y.get("county") or "").strip().replace('臺', '台')
                if c1_norm != c2_norm:
                    continue

                d2 = (y.get("district") or "").strip()

                # 社區名稱吻合度檢查
                comm2 = clean_text(y.get("community_name") or "")
                case2 = clean_text(y.get("case_name") or "")
                addr2 = (y.get("address") or "").strip()
                comm_match = False
                if comm1 and comm2 and (comm1 in comm2 or comm2 in comm1):
                    comm_match = True
                elif comm2 and comm2 in t1:
                    comm_match = True

                # 路名吻合度檢查 (統一 臺/台)
                st1_norm = st1.replace('臺', '台')
                addr2_norm = addr2.replace('臺', '台')
                case2_norm = case2.replace('臺', '台')
                st_match = bool(st1 and (st1_norm in addr2_norm or st1_norm in case2_norm))

                # 行政區檢查 (同縣市跨區放寬)：
                # 若同縣市但行政區不同，若社區完全吻合 或 (路名吻合且兩路段未相距過遠)，放寬行政區判定 (如台中市中平路跨北屯/西屯交界)
                if d1 != d2:
                    if not comm_match and not st_match:
                        continue
                    if not comm_match and is_road_too_far(d1, st1, addr2, max_meters=1500.0):
                        continue

                f2 = int(y.get("from_floor") or 0)
                to_f2 = int(y.get("to_floor") or 0)
                up_f2 = int(y.get("up_floor") or 0)
                to_f1 = int(h.get("to_floor") or f1 or 0)

                # 樓層比對
                if not floor_tolerance:
                    # 嚴格比對 (大樓公寓相容單一樓層與樓中樓區間；透天整棟相容 99、1、-1、0 或 to_floor == up_floor)
                    if f1 > 0 and f1 != 99:
                        effective_to_f2 = to_f2 if to_f2 > 0 else f2
                        floor_match = (
                            f1 == f2 or 
                            to_f1 == effective_to_f2 or 
                            (max(f1, f2) <= min(to_f1, effective_to_f2)) or 
                            (f1 in range(min(f2, effective_to_f2), max(f2, effective_to_f2) + 1)) or 
                            (f2 in range(min(f1, to_f1), max(f1, to_f1) + 1))
                        )
                        if not floor_match:
                            continue
                    elif f1 == 99:
                        if f2 not in [-1, 0, 1, 99] and to_f2 != up_f2:
                            continue
                else:
                    # 樓層容錯比對 (Pass 2)：排除已經相符者
                    effective_to_f2 = to_f2 if to_f2 > 0 else f2
                    is_same_floor = (
                        (f1 == f2 and to_f1 == effective_to_f2) or
                        (f1 == 99 and (f2 in [-1, 0, 1, 99] or to_f2 == up_f2))
                    )
                    if is_same_floor:
                        continue
                    # 必須社區或路名吻合
                    if not comm_match and not st_match:
                        continue
                    # 總價差異必須極小 (<= 80萬 或 <= 5%)
                    p2 = float(y.get("price") or 0.0)
                    if p1 > 0 and p2 > 0:
                        diff_p = abs(p1 - p2)
                        if diff_p > 80.0 and (diff_p / max(p1, p2)) > 0.05:
                            continue

                # 建物登記坪數 (容許誤差 <= 1.5 坪)
                a2 = float(y.get("reg_area") or 0.0)
                diff_area = abs(a1 - a2)
                if diff_area > 1.5:
                    continue

                # 總樓高比對：
                # 1. 若社區吻合，留至第二階段提示 (避免因地下室或頂樓計算出入被誤判下架)
                # 2. 若路名吻合、總價極度接近 (<= 10萬) 且坪數吻合 (<= 1.5坪)，留至第二階段提示 (避免透天豪墅因權狀層數 vs 現況層數計算出入被誤判下架)
                # 3. 其他情況，總樓高容許誤差 <= 1 樓
                p2 = float(y.get("price") or 0.0)
                diff_p = abs(p1 - p2) if (p1 > 0 and p2 > 0) else 999.0
                is_exact_match = st_match and diff_p <= 10.0 and diff_area <= 1.5

                if not comm_match and not is_exact_match and allf1 > 0 and up_f2 > 0:
                    if abs(allf1 - up_f2) > 1:
                        continue
                elif floor_tolerance and not is_exact_match and allf1 > 0 and up_f2 > 0:
                    # 樓層容錯模式下，若非確切同路同價物件，總樓高誤差必須 <= 1 樓
                    if abs(allf1 - up_f2) > 1:
                        continue

                # 屋齡比對與無社區路名交叉防護：
                age2 = float(y.get("build_age") or 0.0)

                # 既無社區吻合、又無路名吻合時的嚴格交叉門檻：
                if not comm_match and not st_match:
                    # 1. 總價差異不可過大 (差價 > 350 萬或相對誤差 > 15%，絕非同一物件)
                    p2 = float(y.get("price") or 0.0)
                    if p1 > 0 and p2 > 0:
                        diff_p = abs(p1 - p2)
                        if diff_p > 350.0 or (diff_p / max(p1, p2)) > 0.15:
                            continue

                    # 2. 屋齡誤差不可超過 1.5 年 (無社區路名時不容許大幅屋齡誤差)
                    if age1 > 0 and age2 > 0:
                        if abs(age1 - age2) > 1.5:
                            continue
                elif not comm_match and not st_match and age1 > 0 and age2 > 0:
                    if abs(age1 - age2) > 3.0:
                        continue

                # 道路經緯度距離定位檢核：若無社區吻合，且兩條路實際相距超過 800 公尺 -> 判定不同物件！
                if not comm_match and is_road_too_far(d1, st1, addr2, max_meters=800.0):
                    continue

                cand_list.append((diff_area, y, comm_match))
            return cand_list

        def check_internal_match(default_reason: str):
            # 檢查是否有同仁手動回報之「內部網頁已上架」紀錄 (全員共享、跨平台比對)
            if internal_list:
                h_fp = generate_property_fingerprint(c1, d1, st1, f1, p1, a1)
                for item in internal_list:
                    matched_internal = False
                    anomaly_msg = ""
                    header_str = "🟢 內部網頁已上架 (同仁手動回報・全員共享)"

                    # 1. 指紋完全吻合
                    if item.get("property_fingerprint") == h_fp:
                        matched_internal = True
                    else:
                        # 2. 核心特徵比對 (縣市、路名/社區/區域、樓層)
                        it_c = clean_text(item.get("county") or "").replace('臺', '台')
                        it_s = clean_text(item.get("street") or "").replace('臺', '台')
                        it_d = clean_text(item.get("district") or "").replace('臺', '台')
                        c1_n = c1.replace('臺', '台')
                        st1_n = st1.replace('臺', '台')

                        if it_c == c1_n:
                            st_match_int = bool(st1_n and (st1_n in it_s or it_s in st1_n))
                            comm_match_int = bool(comm1 and item.get("case_name") and (comm1 in clean_text(item.get("case_name")) or clean_text(item.get("case_name")) in comm1))
                            dist_match_int = bool(d1 and it_d and d1 == it_d)

                            if (st_match_int or comm_match_int) and dist_match_int:
                                it_f = int(item.get("floor") or 0)
                                f_ok = (f1 == it_f) or (f1 in [0, 99, -1] and it_f in [0, 99, -1])
                                if f_ok:
                                    it_p = float(item.get("price") or 0.0)
                                    it_a = float(item.get("reg_area") or 0.0)
                                    diff_price = abs(p1 - it_p)
                                    diff_area = abs(a1 - it_a)

                                    if diff_price <= 50.0 and diff_area <= 1.5:
                                        matched_internal = True
                                    elif diff_price <= 50.0 and diff_area > 1.5:
                                        matched_internal = True
                                        header_str = "🟢 內部網頁已上架 (建坪條件異常提示)"
                                        anomaly_msg = f"• 異常警示：⚠️ 外網刊登【建坪 {a1} 坪】數值異常！(內網登記為 {it_a} 坪，請留意更正)\n"
                                    elif diff_area <= 1.5 and diff_price > 50.0:
                                        matched_internal = True
                                        header_str = "🟢 內部網頁已上架 (開價出入提示)"
                                        p_delta = round(p1 - it_p, 2)
                                        anomaly_msg = f"• 異常警示：⚠️ 外網刊登開價【{p1} 萬】與內網【{it_p} 萬】不一致 (價差 {p_delta} 萬)\n"

                    if matched_internal:
                        case_no = item.get("internal_case_no") or "已查驗在庫"
                        rep_time = item.get("created_at") or "近期"
                        rep_user = item.get("reported_by") or "店頭同仁"
                        notes = item.get("notes") or ""
                        notes_str = f"\n• 備註說明：{notes}" if notes else ""

                        reason = (
                            f"{header_str}\n"
                            f"{anomaly_msg}"
                            f"• 內網案號：{case_no}\n"
                            f"• 回報時間：{rep_time} (由 {rep_user} 確認上架)\n"
                            f"• 說明：此物件在外網雖查無資料，但經同仁查驗已在內部網站上架中。若後續外網正式同步上架，系統將自動納入在庫並畢業清理。{notes_str}"
                        )
                        pseudo_yc = {
                            "case_sid": item.get("internal_case_no") or item.get("source_house_id"),
                            "branch_id": "INTERNAL",
                            "branch_name": f"內網已在庫 ({case_no})",
                            "price": float(item.get("price") or p1),
                            "from_floor": item.get("floor") or f1,
                            "to_floor": item.get("to_floor") or 0,
                            "up_floor": item.get("all_floor") or allf1,
                            "reg_area": float(item.get("reg_area") or a1),
                            "address": f"{item.get('county') or c1}{item.get('district') or d1}{item.get('street') or st1}",
                            "case_name": item.get("case_name") or h.get("title") or "",
                        }
                        return pseudo_yc, "INTERNAL_ACTIVE", reason, 90.0

            return None, "NOT_FOUND", default_reason, 0.0

        candidates = collect_candidates(floor_tolerance=False)
        is_floor_override = False
        if not candidates:
            candidates = collect_candidates(floor_tolerance=True)
            if candidates:
                is_floor_override = True

        if not candidates:
            return check_internal_match("🔴 分店目前無此庫存物件\n• 疑似已成交下架，或未在 11 家加盟店刊登")

        # 若有多筆符合硬性條件，以 特徵吻合度、總價接近度 與 坪數誤差 綜合評分排序
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

            # 樓層完全一致者額外加分 (區間僅交集重疊者保留機會但不加滿分)
            f2 = int(y.get("from_floor") or 0)
            to_f2 = int(y.get("to_floor") or 0)
            effective_to_f2 = to_f2 if to_f2 > 0 else f2
            to_f1 = int(h.get("to_floor") or f1 or 0)
            if f1 == f2 and to_f1 == effective_to_f2:
                score += 15.0

            # 總價接近度評分加權 (零價差或極小價差優先比對)
            p2 = float(y.get("price") or 0.0)
            diff_p = abs(p1 - p2)
            if diff_p < 0.1:
                score += 40.0
            elif diff_p <= 20.0:
                score += 25.0
            elif diff_p <= 50.0:
                score += 15.0
            elif diff_p <= 100.0:
                score += 5.0

            score += max(0.0, 10.0 - diff_a * 5)
            return score

        # 排序：基本總分最高 > 價差最小 > 價格較低者 (用戶需求：如果條件完全一樣 價格取其低做比較)
        candidates.sort(
            key=lambda item: (
                score_cand(item),
                -abs(p1 - float(item[1].get("price") or 0.0)),
                -float(item[1].get("price") or 0.0)
            ),
            reverse=True
        )
        best_diff_a, best_y, comm_match = candidates[0]
        confidence = min(100.0, score_cand((best_diff_a, best_y, comm_match)))

        # 2. 第二階段：細節差異診斷
        anomalies = []
        p2 = float(best_y.get("price") or 0.0)
        allf2 = int(best_y.get("up_floor") or 0)
        age2 = float(best_y.get("build_age") or 0.0)
        addr2 = (best_y.get("address") or "").strip()
        case2 = (best_y.get("case_name") or "").strip()

        # 尋找在庫中「同樓層/同社區」的其他候選戶別 (供使用者比對參考)
        best_sid = best_y.get("case_sid")
        best_f2 = int(best_y.get("from_floor") or 0)
        best_comm = clean_text(best_y.get("community_name") or "")
        siblings = []
        for y in yc_list:
            if y.get("case_sid") == best_sid:
                continue
            if (y.get("county") or "").strip().replace('臺', '台') != c1.replace('臺', '台'):
                continue
            y_f2 = int(y.get("from_floor") or 0)
            y_to_f2 = int(y.get("to_floor") or 0)
            y_eff_f2 = y_to_f2 if y_to_f2 > 0 else y_f2

            is_same_f = False
            if best_f2 > 0 and best_f2 != 99:
                is_same_f = (y_f2 == best_f2 or y_eff_f2 == best_f2 or y_f2 == f1 or y_eff_f2 == f1)
            elif best_f2 == 99 or f1 == 99:
                is_same_f = (y_f2 in [-1, 0, 1, 99])
            if not is_same_f:
                continue

            y_comm2 = clean_text(y.get("community_name") or "")
            y_case2 = clean_text(y.get("case_name") or "")
            y_addr2 = (y.get("address") or "").strip().replace('臺', '台')

            c_match = False
            if comm1 and y_comm2 and (comm1 in y_comm2 or y_comm2 in comm1):
                c_match = True
            elif best_comm and y_comm2 and (best_comm in y_comm2 or y_comm2 in best_comm):
                c_match = True
            elif y_comm2 and (y_comm2 in t1 or y_comm2 in clean_text(best_y.get("case_name") or "")):
                c_match = True

            if (comm1 or best_comm):
                if not c_match:
                    continue
            else:
                s_match = bool(st1 and (st1.replace('臺', '台') in y_addr2 or st1.replace('臺', '台') in y_case2))
                if not s_match:
                    continue

            # 坪數差距在合理範圍內 (大樓嚴格限制 <= 2.0 坪，別墅透天 <= 5.0 坪，避免坪數落差過大之不同房型混入)
            y_a2 = float(y.get("reg_area") or 0.0)
            max_area_diff = 5.0 if (f1 == 99 or best_f2 == 99) else 2.0
            if abs(a1 - y_a2) > max_area_diff:
                continue
            siblings.append(y)

        siblings.sort(key=lambda item: float(item.get("price") or 0.0))
        sibling_note = ""
        if siblings:
            sibling_lines = ["\n💡 在庫另有同樓層/同社區多戶候選參考："]
            for s in siblings[:3]:
                s_p = float(s.get("price") or 0.0)
                s_a = float(s.get("reg_area") or 0.0)
                s_f = int(s.get("from_floor") or 0)
                s_f_disp = f"{s_f}樓" if s_f != 99 else "整棟"
                s_name = (s.get("case_name") or "").strip()
                s_sid = s.get("case_sid")
                sibling_lines.append(f"• 永慶案名：{s_name} (SID: {s_sid})｜開價 {s_p}萬｜{s_f_disp}｜{s_a}坪")
            sibling_note = "\n" + "\n".join(sibling_lines)

        def make_return(st: str, rz: str):
            final_reason = (rz + sibling_note) if sibling_note else rz
            return best_y, st, final_reason, confidence

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

        # 樓層出入判斷 (若樓層為區間交集而非完全一致，例如 16~17樓 vs 17樓，提示樓中樓 vs 單一樓層規劃出入)
        has_floor_range_diff = False
        f2 = int(best_y.get("from_floor") or 0)
        to_f2 = int(best_y.get("to_floor") or 0)
        effective_to_f2 = to_f2 if to_f2 > 0 else f2
        to_f1 = int(h.get("to_floor") or f1 or 0)

        if f1 > 0 and f1 != 99 and f2 != 99:
            if f1 != f2 or to_f1 != effective_to_f2:
                has_floor_range_diff = True
                f1_desc = f"{f1}~{to_f1}樓" if to_f1 > f1 else f"{f1}樓"
                f2_desc = f"{f2}~{effective_to_f2}樓" if effective_to_f2 > f2 else f"{f2}樓"
                anomalies.append(f"樓層規劃出入：{platform_name} 標「{f1_desc}」 vs 永慶標「{f2_desc}」 (疑似樓中樓 vs 單一樓層規劃出入)")

        # 特殊判斷 0：樓層容錯模式 (其他主要條件皆吻合，僅樓層不同，如經紀人避諱4樓改標或戶別出入)
        if is_floor_override:
            status = "SPEC_MISMATCH"
            f1_desc = f"{f1}~{to_f1}樓" if to_f1 > f1 else (f"{f1}樓" if f1 != 99 else "整棟")
            f2_desc = f"{f2}~{effective_to_f2}樓" if effective_to_f2 > f2 else (f"{f2}樓" if f2 != 99 else "整棟")
            reason = (
                f"⚠️ 規格不符提示：樓層標示出入 (無法完全判定為同一物件)\n"
                f"• {platform_name} 標示樓層：{f1_desc} vs 永慶分店標示：{f2_desc}\n"
                f"• 因樓層出入（疑似避諱4樓改標或不同戶別），無法直接判定為同一物件，故歸納為【規格不符】提示供人員查核確認\n"
                f"• 其他條件吻合：社區名稱、總價 ({p1}萬)、建坪 ({a1}坪)、總樓高 ({allf1 or allf2}樓)、路名等條件均高度相符"
            )
            return make_return(status, reason)

        # 特殊判斷 1：僅屋齡有出入，其他條件 (總價/建坪/樓層/總高/路名) 皆吻合
        if not has_price_diff and not has_allf_diff and not has_street_diff and not has_floor_range_diff and has_age_diff:
            status = "SPEC_MISMATCH"
            f_disp = f"{f1}樓" if f1 != 99 else "整棟"
            reason = (
                f"💡 提示：屋齡有出入 (疑似人員Key錯屋齡)\n"
                f"• {platform_name} 標示屋齡：{age1} 年 vs 永慶標示屋齡：{age2} 年\n"
                f"• 總價 ({p1}萬)、建坪 ({a1}坪)、樓層 ({f_disp})、總樓高 ({allf1}樓)、路名等其他條件皆完全相符"
            )
            return make_return(status, reason)

        # 特殊判斷 2：僅總樓高有出入，其他條件 (總價/建坪/樓層/屋齡/路名) 皆吻合
        if not has_price_diff and not has_age_diff and not has_street_diff and not has_floor_range_diff and has_allf_diff:
            status = "SPEC_MISMATCH"
            f_disp = f"{f1}樓" if f1 not in [0, 99] else "整棟"
            reason = (
                f"⚠️ 規格不符提示：總樓高出入 (疑似含地下室或頂樓增建計算差異)\n"
                f"• {platform_name} 標 {allf1} 樓高 vs 永慶分店標 {allf2} 樓高\n"
                f"• 總價 ({p1}萬)、建坪 ({a1}坪)、樓層 ({f_disp})、屋齡 ({age1}年)、路名皆完全相符"
            )
            return make_return(status, reason)

        # 特殊判斷 3：縣市區域、建坪、樓層、總價、總樓高、屋齡都一樣，只有路名不同
        if not has_price_diff and not has_allf_diff and not has_age_diff and not has_floor_range_diff and has_street_diff:
            status = "SPEC_MISMATCH"
            reason = (
                f"💡 提示：是否是路名有誤 (極少機會 但也有可能)\n"
                f"• 縣市/區域/樓層/建坪/總價/總高皆一致\n"
                f"• {platform_name} 路名:「{st1}」\n"
                f"• 永慶地址:「{addr2}」"
            )
            return make_return(status, reason)

        # 特殊判斷 4：僅樓層規劃有區間出入 (無價差/無總高差/無屋齡差/無路名差)
        if not has_price_diff and not has_allf_diff and not has_age_diff and not has_street_diff and has_floor_range_diff:
            status = "SPEC_MISMATCH"
            f1_desc = f"{f1}~{to_f1}樓" if to_f1 > f1 else f"{f1}樓"
            f2_desc = f"{f2}~{effective_to_f2}樓" if effective_to_f2 > f2 else f"{f2}樓"
            reason = (
                f"💡 提示：樓層規劃出入 (疑似樓中樓 vs 單一樓層規劃出入)\n"
                f"• {platform_name} 標示樓層：{f1_desc} vs 永慶標示樓層：{f2_desc}\n"
                f"• 兩者樓層雖有交集，但單層與樓中樓規劃不同，可能為不同戶別或案源出入\n"
                f"• 總價 ({p1}萬)、建坪 ({a1}坪)、屋齡 ({age1}年)、總樓高皆相符"
            )
            return make_return(status, reason)

        # 多重異常排除 (黃金準則：若出現 3 個或以上錯誤，或無社區吻合且路名出入+顯著價差，判定為不同物件 NOT_FOUND，回落內部在庫檢查)
        if len(anomalies) >= 3:
            return check_internal_match("🔴 分店目前無此庫存物件\n• 疑似已成交下架，或未在 11 家加盟店刊登 (多項關鍵特徵出入過大，判定非同一物件)")

        if not comm_match and has_street_diff and has_price_diff and (abs(diff_price) >= 200.0 or abs(diff_price) / max(p1, p2) >= 0.08):
            return check_internal_match("🔴 分店目前無此庫存物件\n• 疑似已成交下架，或未在 11 家加盟店刊登 (路名不同且價差過大，判定非同一物件)")

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

        return make_return(status, reason)

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

        # 3.2 取得同仁手動回報之「內部網頁已上架」庫存 (全員共享、跨平台比對)
        internal_list = []
        try:
            internal_list = self.db.execute(
                "SELECT * FROM internal_active_houses WHERE status = 'ACTIVE';"
            )
            if internal_list:
                print(f"📌 已載入同仁手動回報內網在庫物業: {len(internal_list)} 筆")
        except Exception as e:
            print(f"[Warn] 讀取 internal_active_houses 失敗: {e}")

        print(f"{platform} 待比對: {len(std_houses)} 筆 | 永慶分店在庫: {yc_inventory_count} 筆 (最新同步: {yc_synced_at})")

        matched_count = 0
        price_diff_count = 0
        not_found_count = 0
        spec_diff_count = 0
        internal_active_count = 0
        report_items = []

        for h in std_houses:
            p_val = float(h.get("price") or 0.0)
            hid = str(h.get("house_id"))
            title = h.get("title") or ""
            area_val = float(h.get("reg_area") or h.get("land_area") or 0.0)
            f_val = h.get("floor")
            allf_val = h.get("all_floor")
            to_f_val = h.get("to_floor")

            is_land = bool(h.get("is_land")) or (h.get("case_type") == "土地")

            if is_land:
                f_str = "土地"
            elif to_f_val and to_f_val > f_val and f_val != 99:
                f_str = f"{f_val}~{to_f_val}/{allf_val}樓"
            else:
                f_str = f"{f_val}/{allf_val}" if f_val != 99 else f"整棟/{allf_val}"

            comm_val = h.get("community_name") or ""
            cover_val = h.get("cover") or ""
            h_url = h.get("url") or ""
            addr_val = f"{h.get('county') or ''}{h.get('district') or ''}{h.get('street') or ''}".strip()

            best_match, status, reason, confidence = self.match_house(h, yc_list, internal_list=internal_list)

            if status == "NORMAL":
                matched_count += 1
                # 若該物件外網已正常在售，自動畢業清理 internal_active_houses 內網暫存
                try:
                    h_fp = generate_property_fingerprint(
                        h.get("county"), h.get("district"), h.get("street"),
                        h.get("floor"), h.get("price"), area_val,
                        is_land=is_land
                    )
                    self.db.execute(
                        "DELETE FROM internal_active_houses WHERE status = 'ACTIVE' AND property_fingerprint = ?;",
                        [h_fp]
                    )
                except Exception as e:
                    print(f"[Warn] 自動畢業清理 internal_active_houses 失敗: {e}")
            elif status == "INTERNAL_ACTIVE":
                internal_active_count += 1
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
                b_url = f"https://buy.yungching.com.tw/house/{sid}" if str(sid).isdigit() else ""

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
                price_diff_count, not_found_count, spec_diff_count, internal_active_count,
                created_at, platform, yc_inventory_count, yc_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [
                report_id, shop_id, full_shop_url, user_id, len(std_houses), matched_count,
                price_diff_count, not_found_count, spec_diff_count, internal_active_count,
                now_str, platform, yc_inventory_count, yc_synced_at
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
        print(f"  🟢 內網已上架 (同仁回報): {internal_active_count} 筆")
        print(f"  🔴 查無外網資料: {not_found_count} 筆")
        print(f"  🟡 價格調整/不一致: {price_diff_count} 筆")
        print(f"  🟠 規格出入 (樓層/總高/屋齡/路名): {spec_diff_count} 筆")
        print(f"  總異常件數: {abnormal_total} 筆")

        return report_id

if __name__ == "__main__":
    comparator = InventoryComparator()
    # 測試 5168
    comparator.run_comparison("5168", "0968682023")
