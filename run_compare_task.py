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
    if "rakuya.com.tw" in s or "rakuya" in s:
        return "rakuya"
    return "591"

def build_interactive_message(db_client: Optional[TursoClient], user_id: str, message: str, liff_url: str = "") -> dict:
    """產生包含店鋪查詢按鈕與 LIFF 連結的 Flex Message 互動式卡片 (相容 PC 與手機)"""
    buttons = []
    quick_items = []
    
    if db_client and user_id:
        try:
            rows = db_client.execute("SELECT * FROM user_saved_shops WHERE user_id = ? LIMIT 1;", [user_id])
            if rows:
                r = rows[0]
                if r.get("url_591"):
                    cnt = r.get("count_591") or 0
                    lbl = f"🔍 查 591 ({cnt}次)"
                    txt = r.get("url_591")
                    buttons.append({
                        "type": "button",
                        "style": "primary",
                        "color": "#ff7700",
                        "height": "sm",
                        "action": {"type": "message", "label": lbl, "text": txt}
                    })
                    quick_items.append({"type": "action", "action": {"type": "message", "label": lbl, "text": txt}})
                
                if r.get("url_5168"):
                    cnt = r.get("count_5168") or 0
                    lbl = f"🔍 查 5168 ({cnt}次)"
                    txt = r.get("url_5168")
                    buttons.append({
                        "type": "button",
                        "style": "primary",
                        "color": "#0084ff",
                        "height": "sm",
                        "action": {"type": "message", "label": lbl, "text": txt}
                    })
                    quick_items.append({"type": "action", "action": {"type": "message", "label": lbl, "text": txt}})

                if r.get("url_rakuya"):
                    cnt = r.get("count_rakuya") or 0
                    lbl = f"🔍 查 樂屋網 ({cnt}次)"
                    txt = r.get("url_rakuya")
                    buttons.append({
                        "type": "button",
                        "style": "primary",
                        "color": "#e60012",
                        "height": "sm",
                        "action": {"type": "message", "label": lbl, "text": txt}
                    })
                    quick_items.append({"type": "action", "action": {"type": "message", "label": lbl, "text": txt}})
        except Exception as e:
            print(f"[Warn] 查詢已存店鋪按鈕失敗: {e}")

    if liff_url:
        buttons.append({
            "type": "button",
            "style": "secondary",
            "height": "sm",
            "action": {"type": "uri", "label": "📊 開啟 LIFF 儀表板", "uri": liff_url}
        })

    if not buttons:
        res_msg = {"type": "text", "text": message}
        if liff_url:
            res_msg["text"] += f"\n\n📊 點擊開啟 LIFF 比對儀表板：\n{liff_url}"
        return res_msg

    alt_text = message.split("\n")[0] if message else "店鋪比對報告"
    if len(alt_text) > 40:
        alt_text = alt_text[:37] + "..."

    # 決定 Header 橫幅：依使用者需求，只有庫存比對完成且有帶入 LIFF 儀表板時，最上方才顯示綠色 Header
    header_color = None
    header_title = None

    if liff_url and ("比對完成" in message or "比對報告" in message or "比對結果總覽" in message):
        if "快取" in message:
            header_color = "#00b900"
            header_title = "⚡ 庫存比對完成（快取紀錄 ✔）"
        else:
            header_color = "#06C755"
            header_title = "✅ 庫存比對完成 ✔"

    if header_title:
        alt_text = header_title

    bubble_contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": message,
                    "size": "sm",
                    "wrap": True,
                    "lineSpacing": "5px"
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": buttons
        }
    }

    if header_color and header_title:
        bubble_contents["header"] = {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": header_color,
            "paddingTop": "14px",
            "paddingBottom": "14px",
            "contents": [
                {
                    "type": "text",
                    "text": header_title,
                    "weight": "bold",
                    "size": "md",
                    "color": "#ffffff"
                }
            ]
        }

    flex_msg = {
        "type": "flex",
        "altText": alt_text,
        "contents": bubble_contents
    }

    if quick_items:
        flex_msg["quickReply"] = {"items": quick_items}

    return flex_msg

def format_compare_summary(platform: str, shop_id: str, total: int, matched: int, not_found: int, price_diff: int, spec_diff: int, is_cache: bool = False) -> str:
    """產生包含清晰打勾符號與分隔區塊的精美比對報告文字"""
    plat_display = "5168 房仲店鋪" if platform == "5168" else ("樂屋網 VIP 店鋪" if platform == "rakuya" else "591 店鋪")
    abnormal = not_found + price_diff + spec_diff
    cache_note = "（距前次比對未滿 1 分鐘，回傳最新快取紀錄）\n\n" if is_cache else ""

    if abnormal == 0:
        abnormal_detail = "  🎉 太棒了！全部物件皆與分店一致，未發現任何異常 ✔"
        status_note = "🎉 全數正常 ✔"
    else:
        status_note = f"⚠️ 發現 {abnormal} 件異常"
        abnormal_detail = (
            f"  • ❌ 查無分店庫存：{not_found} 件 (疑似已下架)\n"
            f"  • 💰 總價標價出入：{price_diff} 件 (開價不一致)\n"
            f"  • 📐 規格/樓層出入：{spec_diff} 件 (屋齡/樓層出入)"
        )

    return (
        f"{cache_note}"
        f"🏢 比對平台：{plat_display}\n"
        f"📍 店鋪代號：{shop_id}\n"
        f"📦 在庫總數：{total} 件\n\n"
        f"【📊 庫存比對結果總覽】\n"
        f"✔ 🟢 正常在售：{matched} 件 (與分店庫存相符)\n"
        f"❗ ⚠️ 異常物件：{abnormal} 件 ({status_note})\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"【🔍 異常類別明細】\n"
        f"{abnormal_detail}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💡 點擊下方【📊 開啟 LIFF 儀表板】可查看完整物件診斷與下架標註！"
    )

def send_line_push_notification(user_id: str, message: str, liff_url: str = "", db_client: Optional[TursoClient] = None):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
    if not token or not user_id:
        print("[Notice] 未設定 LINE_CHANNEL_ACCESS_TOKEN 或無 USER_ID，略過推播發送。")
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    msg_obj = build_interactive_message(db_client, user_id, message, liff_url)

    payload = {
        "to": user_id,
        "messages": [msg_obj]
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            print(f"✅ LINE 推播通知已成功發送至: {user_id}")
        else:
            print(f"[Warn] LINE 推播失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"[Error] 發送 LINE 推播發生錯誤: {e}")

def release_user_query_lock(db_client: TursoClient, user_id: str):
    """解除使用者當前查詢併發鎖定"""
    if not db_client or not user_id:
        return
    try:
        db_client.execute(
            "UPDATE user_query_locks SET is_running = 0 WHERE user_id = ?;",
            [user_id]
        )
        print(f"🔓 [UserLock] 已成功釋放使用者 {user_id} 查詢鎖定。")
    except Exception as e:
        print(f"[Warn] 釋放使用者查詢鎖定失敗: {e}")

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
    platform = (
        payload.get("platform") or 
        inputs.get("platform") or 
        detect_platform(shop_input)
    )

    shop_url = payload.get("shop_url") or os.environ.get("SHOP_URL") or shop_input
    if shop_url == "true" or not shop_url.startswith("http"):
        if platform == "5168":
            shop_url = f"https://realtor.houseprice.tw/agent/buy/{shop_input}/"
        elif platform == "rakuya":
            shop_url = f"https://vip.rakuya.com.tw/{shop_input}/sell"
        elif platform == "591":
            shop_url = f"https://www.591.com.tw/broker{shop_input}"

    print(f"[*] 偵測到目標平台: {platform} (輸入: {shop_input}, 店鋪網址: {shop_url})")

    db = TursoClient()
    comparator = InventoryComparator(db)

    # 0. 檢查永慶分店庫存是否正在每六小時刷新鎖定中
    sync_lock = comparator.check_branch_sync_lock()
    if sync_lock:
        lock_msg = sync_lock.get("message") or "目前刷新分店庫存中 請五分鐘後再查詢"
        print(f"[Notice] 系統正在刷新永慶分店庫存中，暫停比對任務: {lock_msg}")
        send_line_push_notification(user_id, f"⚠️ {lock_msg}", db_client=db)
        release_user_query_lock(db, user_id)
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

        summary_msg = format_compare_summary(
            platform=platform,
            shop_id=shop_id,
            total=total,
            matched=matched,
            not_found=not_found,
            price_diff=price_diff,
            spec_diff=spec_diff,
            is_cache=True
        )
        liff_target_url = f"{liff_base}?report_id={report_id}" if liff_base else ""
        send_line_push_notification(user_id, summary_msg, liff_url=liff_target_url, db_client=db)
        release_user_query_lock(db, user_id)
        return

    # 3. 爬取最新庫存並轉換為標準格式
    if platform == "5168":
        shop_id, full_shop_url, std_houses = crawler_5168.crawl_and_get_standard_houses(shop_input)
    elif platform == "rakuya":
        shop_id, full_shop_url, std_houses = crawler_rakuya.crawl_and_get_standard_houses(shop_input)
    else:
        shop_id, full_shop_url, std_houses = crawler_591.crawl_and_get_standard_houses(shop_input)

    target_shop_url = shop_url or full_shop_url

    if not std_houses:
        err_msg = f"⚠️ 無法取得【{platform}】店鋪庫存資料（店鋪代號: {shop_id}）。\n\n可能原因：該店鋪目前無在售物件，或平台連線異常，請稍後再試。"
        print(f"[Warn] {err_msg}")
        send_line_push_notification(user_id, err_msg, db_client=db)
        release_user_query_lock(db, user_id)
        return

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

        summary_msg = format_compare_summary(
            platform=platform,
            shop_id=shop_id,
            total=total,
            matched=matched,
            not_found=not_found,
            price_diff=price_diff,
            spec_diff=spec_diff,
            is_cache=False
        )
        liff_target_url = f"{liff_base}?report_id={report_id}" if liff_base else ""
        send_line_push_notification(user_id, summary_msg, liff_url=liff_target_url, db_client=db)
        release_user_query_lock(db, user_id)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        print(f"[FATAL ERROR IN RUN_COMPARE_TASK]\n{err_detail}", file=sys.stderr)
        
        # 嘗試推播失敗訊息給使用者並釋放查詢鎖
        try:
            raw_payload = os.environ.get("EVENT_PAYLOAD")
            payload = json.loads(raw_payload) if raw_payload else {}
            raw_inputs = os.environ.get("WORKFLOW_INPUTS")
            inputs = json.loads(raw_inputs) if raw_inputs else {}
            user_id = payload.get("user_id") or inputs.get("user_id") or os.environ.get("USER_ID", "")
            if user_id:
                try:
                    db_fallback = TursoClient()
                except Exception:
                    db_fallback = None
                send_line_push_notification(
                    user_id,
                    f"⚠️ 店鋪比對執行異常：\n{str(e)}\n\n請檢查店鋪網址或稍後再試。",
                    db_client=db_fallback
                )
                if db_fallback:
                    try:
                        release_user_query_lock(db_fallback, user_id)
                    except Exception:
                        pass
        except Exception as notify_err:
            print(f"[Warn] 發送失敗推播失敗: {notify_err}")
        sys.exit(1)
