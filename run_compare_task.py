# -*- coding: utf-8 -*-
from __future__ import annotations
"""
GitHub Actions 觸發之跨平台店鋪比對執行入口 (Multi-Platform Compare Task Runner)
支援 591、5168 及未來擴充平台之自動識別、爬取、特徵比對與 LINE 推播通知
"""

import os
import sys
import json
import requests
from crawler_591 import T591ShopCrawler
from crawler_5168 import HousePrice5168Crawler
from crawler_rakuya import RakuyaShopCrawler
from comparator import InventoryComparator
from turso_client import TursoClient

def detect_platform(input_str: str) -> str:
    s = input_str.lower()
    if "houseprice.tw" in s or "5168" in s:
        return "5168"
    if "rakuya.com.tw" in s:
        return "rakuya"
    return "591"

def send_line_push_notification(user_id: str, message: str, liff_url: str = ""):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token or not user_id:
        print("[Notice] 未設定 LINE_CHANNEL_ACCESS_TOKEN 或無 USER_ID，略過推播發送。")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    messages = [{"type": "text", "text": message}]
    if liff_url:
        messages.append({
            "type": "text",
            "text": f"📊 點擊開啟 LIFF 比對儀表板：\n{liff_url}"
        })

    payload = {
        "to": user_id,
        "messages": messages
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            print(f"✅ LINE 推播通知已成功發送至: {user_id}")
        else:
            print(f"[Warn] LINE 推播失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"[Error] 發送 LINE 推播發生錯誤: {e}")

def main():
    payload = {}
    raw_payload = os.environ.get("EVENT_PAYLOAD")
    if raw_payload:
        try:
            payload = json.loads(raw_payload) or {}
        except Exception as e:
            print(f"[Warn] 解析 EVENT_PAYLOAD 失敗: {e}")

    inputs = {}
    raw_inputs = os.environ.get("WORKFLOW_INPUTS")
    if raw_inputs:
        try:
            inputs = json.loads(raw_inputs) or {}
        except Exception as e:
            print(f"[Warn] 解析 WORKFLOW_INPUTS 失敗: {e}")

    # 優先從 JSON payload 取得乾淨的 shop_url 或 shop_id，避免 GitHub Actions 表達式強制轉為 'true'
    shop_input = (
        payload.get("shop_url") or 
        payload.get("shop_id") or 
        inputs.get("shop_id") or 
        os.environ.get("SHOP_INPUT")
    )
    if not shop_input and len(sys.argv) > 1:
        shop_input = sys.argv[1]

    if not shop_input or shop_input == "true":
        print(f"[Error] 無效的 SHOP_INPUT 參數: {shop_input}")
        sys.exit(1)

    user_id = (
        payload.get("user_id") or 
        inputs.get("user_id") or 
        os.environ.get("USER_ID", "")
    )
    liff_base = os.environ.get("LIFF_URL", "")
    shop_url = payload.get("shop_url") or os.environ.get("SHOP_URL") or shop_input
    if shop_url == "true":
        shop_url = shop_input

    platform = detect_platform(shop_input)
    print(f"[*] 偵測到目標平台: {platform} (輸入: {shop_input})")

    db = TursoClient()
    comparator = InventoryComparator(db)

    # 0. 檢查永慶分店庫存是否正在每六小時刷新鎖定中
    sync_lock = comparator.check_branch_sync_lock()
    if sync_lock:
        lock_msg = sync_lock.get("message") or "目前刷新分店庫存中 請五分鐘後再查詢"
        print(f"[Notice] 系統正在刷新永慶分店庫存中，暫停比對任務: {lock_msg}")
        send_line_push_notification(user_id, f"⚠️ {lock_msg}")
        return

    # 1. 根據平台萃取店鋪 ID / 帳號
    if platform == "5168":
        crawler_5168 = HousePrice5168Crawler(db)
        account, account_type = crawler_5168.extract_account_info(shop_input)
        shop_id = account
    elif platform == "rakuya":
        crawler_rakuya = RakuyaShopCrawler(db)
        shop_id = crawler_rakuya.extract_shop_id(shop_input)
    else:
        crawler_591 = T591ShopCrawler(db)
        shop_id = crawler_591.extract_shop_id(shop_input)

    # 2. 檢查 1 分鐘內快取
    cached_rpt = comparator.check_recent_cache(shop_id, platform=platform, user_id=user_id, max_seconds=60)
    if cached_rpt:
        report_id = cached_rpt["report_id"]
        total = cached_rpt.get("total_591_count", 0)
        matched = cached_rpt.get("matched_count", 0)
        not_found = cached_rpt.get("not_found_count", 0)
        price_diff = cached_rpt.get("price_diff_count", 0)
        spec_diff = cached_rpt.get("spec_diff_count", 0)
        abnormal = not_found + price_diff + spec_diff

        summary_msg = (
            f"⚡【{platform} 店鋪比對報告（快取紀錄）】\n"
            f"（距前次比對未滿 1 分鐘，直接回傳最新結果）\n\n"
            f"📍 店鋪代號: {shop_id}\n"
            f"📦 {platform} 總在售: {total} 件\n"
            f"🟢 正常在售: {matched} 件\n"
            f"⚠️ 異常物件: {abnormal} 件\n"
            f"  • 查無分店庫存 (疑似下架): {not_found} 件\n"
            f"  • 總價標價不一致: {price_diff} 件\n"
            f"  • 規格/樓層/屋齡出入: {spec_diff} 件"
        )
        liff_target_url = f"{liff_base}?report_id={report_id}" if liff_base else ""
        send_line_push_notification(user_id, summary_msg, liff_target_url)
        return

    # 3. 爬取最新庫存並轉換為標準格式
    if platform == "5168":
        shop_id, full_shop_url, std_houses = crawler_5168.crawl_and_get_standard_houses(shop_input)
    elif platform == "rakuya":
        shop_id, full_shop_url, std_houses = crawler_rakuya.crawl_and_get_standard_houses(shop_input)
    else:
        shop_id, full_shop_url, std_houses = crawler_591.crawl_and_get_standard_houses(shop_input)

    target_shop_url = shop_url or full_shop_url

    # 4. 執行通用特徵比對
    report_id = comparator.run_comparison(
        platform=platform,
        shop_id=shop_id,
        houses=std_houses,
        shop_url=target_shop_url,
        user_id=user_id
    )

    # 5. 查詢統計並發送推播通知
    report = db.execute("SELECT * FROM compare_reports WHERE report_id = ?;", [report_id])
    if report:
        r = report[0]
        total = r.get("total_591_count", 0)
        matched = r.get("matched_count", 0)
        not_found = r.get("not_found_count", 0)
        price_diff = r.get("price_diff_count", 0)
        spec_diff = r.get("spec_diff_count", 0)
        abnormal = not_found + price_diff + spec_diff

        summary_msg = (
            f"🔔【{platform} 店鋪庫存比對完成】\n\n"
            f"📍 店鋪代號: {shop_id}\n"
            f"📦 {platform} 總在售: {total} 件\n"
            f"🟢 正常在售: {matched} 件\n"
            f"⚠️ 異常物件: {abnormal} 件\n"
            f"  • 查無分店庫存 (疑似下架): {not_found} 件\n"
            f"  • 總價標價不一致: {price_diff} 件\n"
            f"  • 規格/樓層/屋齡出入: {spec_diff} 件"
        )
        liff_target_url = f"{liff_base}?report_id={report_id}" if liff_base else ""
        send_line_push_notification(user_id, summary_msg, liff_target_url)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        print(f"[FATAL ERROR IN RUN_COMPARE_TASK]\n{err_detail}", file=sys.stderr)
        
        # 嘗試推播失敗訊息給使用者
        try:
            raw_payload = os.environ.get("EVENT_PAYLOAD")
            payload = json.loads(raw_payload) if raw_payload else {}
            raw_inputs = os.environ.get("WORKFLOW_INPUTS")
            inputs = json.loads(raw_inputs) if raw_inputs else {}
            user_id = payload.get("user_id") or inputs.get("user_id") or os.environ.get("USER_ID", "")
            if user_id:
                send_line_push_notification(
                    user_id,
                    f"⚠️ 店鋪比對執行異常：\n{str(e)}\n\n請檢查店鋪網址或稍後再試。"
                )
        except Exception as notify_err:
            print(f"[Warn] 發送失敗推播失敗: {notify_err}")
        sys.exit(1)
