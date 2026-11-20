import os
import sys
import requests
from crawler_591 import T591ShopCrawler
from comparator import InventoryComparator
from turso_client import TursoClient

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
    shop_input = os.environ.get("SHOP_INPUT")
    if not shop_input and len(sys.argv) > 1:
        shop_input = sys.argv[1]

    if not shop_input:
        print("[Error] 缺少 SHOP_INPUT 參數。")
        sys.exit(1)

    user_id = os.environ.get("USER_ID", "")
    liff_base = os.environ.get("LIFF_URL", "")

    db = TursoClient()
    crawler = T591ShopCrawler(db)
    shop_id = crawler.extract_shop_id(shop_input)

    # 檢查是否在 1 分鐘內重複查詢
    comparator = InventoryComparator(db)
    cached_rpt = comparator.check_recent_cache(shop_id, user_id=user_id, max_seconds=60)

    if cached_rpt:
        report_id = cached_rpt["report_id"]
        total = cached_rpt.get("total_591_count", 0)
        matched = cached_rpt.get("matched_count", 0)
        not_found = cached_rpt.get("not_found_count", 0)
        price_diff = cached_rpt.get("price_diff_count", 0)
        spec_diff = cached_rpt.get("spec_diff_count", 0)
        abnormal = not_found + price_diff + spec_diff

        summary_msg = (
            f"⚡【591 店鋪比對報告（快取紀錄）】\n"
            f"（距前次比對未滿 1 分鐘，直接回傳最新結果）\n\n"
            f"📍 店鋪代號: {shop_id}\n"
            f"📦 591 總在售: {total} 件\n"
            f"🟢 正常在售: {matched} 件\n"
            f"⚠️ 異常物件: {abnormal} 件\n"
            f"  • 查無分店庫存 (疑似下架): {not_found} 件\n"
            f"  • 總價標價不一致: {price_diff} 件\n"
            f"  • 規格/樓層/屋齡出入: {spec_diff} 件"
        )
        liff_target_url = f"{liff_base}?report_id={report_id}" if liff_base else ""
        send_line_push_notification(user_id, summary_msg, liff_target_url)
        return

    # 若無快取，進行完整爬取與比對
    crawler.crawl_and_save(shop_input)
    report_id = comparator.run_comparison(shop_id, shop_url=shop_input, user_id=user_id)

    # 查詢統計並發送通知
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
            f"🔔【591 店鋪庫存比對完成】\n\n"
            f"📍 店鋪代號: {shop_id}\n"
            f"📦 591 總在售: {total} 件\n"
            f"🟢 正常在售: {matched} 件\n"
            f"⚠️ 異常物件: {abnormal} 件\n"
            f"  • 查無分店庫存 (疑似下架): {not_found} 件\n"
            f"  • 總價標價不一致: {price_diff} 件\n"
            f"  • 規格/樓層/屋齡出入: {spec_diff} 件"
        )
        liff_target_url = f"{liff_base}?report_id={report_id}" if liff_base else ""
        send_line_push_notification(user_id, summary_msg, liff_target_url)

if __name__ == "__main__":
    main()
