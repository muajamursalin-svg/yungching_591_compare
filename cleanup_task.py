# -*- coding: utf-8 -*-
"""
定期資料清理任務 (Daily Database Cleanup Task)
每日常態清理：
1. 超過指定保留天數 (預設 3 天) 的比對報告與明細 (compare_reports, compare_items)
2. 超過保留天數的平台物件爬蟲快照 (t591_houses, t5168_houses, trakuya_houses)
3. 標記已下架 (REMOVED) 或外網已正式上架畢業 (GRADUATED) 超過保留天數的內網回報垃圾紀錄
4. 清理超時或遺留的過期查詢鎖定 (user_query_locks, system_locks)

🔒 重要防護：
- 永慶分店在庫總表 (yungching_houses) 絕對保留
- 使用者常用店鋪記憶與捷徑 (user_saved_shops) 絕對保留
- 狀態仍在庫有效中 (status = 'ACTIVE') 的內網回報物業絕對保留
"""

import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from turso_client import TursoClient

TAIWAN_TZ = timezone(timedelta(hours=8))

def get_tw_now() -> datetime:
    return datetime.now(TAIWAN_TZ)

def run_cleanup(retention_days: int = 3, db_client: Optional[TursoClient] = None) -> Dict[str, Any]:
    """
    執行定期清理
    :param retention_days: 搜尋與比對資料保留天數 (預設 3 天)
    :param db_client: TursoClient 實例
    """
    db = db_client or TursoClient()
    now = get_tw_now()
    cutoff_dt = now - timedelta(days=retention_days)
    cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n================ 開始執行定期資料清理任務 ================")
    print(f"執行時間: {now.strftime('%Y-%m-%d %H:%M:%S')} (台灣時間)")
    print(f"保留政策: 保留最近 {retention_days} 天資料")
    print(f"清理基準截止時間: {cutoff_str}")

    stats = {
        "reports_deleted": 0,
        "items_deleted": 0,
        "t591_houses_deleted": 0,
        "t5168_houses_deleted": 0,
        "trakuya_houses_deleted": 0,
        "internal_archived_deleted": 0,
        "locks_cleared": 0
    }

    try:
        # 1. 查詢即將清理的比對報告 ID
        expired_reports = db.execute(
            "SELECT report_id FROM compare_reports WHERE created_at < ?;",
            [cutoff_str]
        )
        report_ids = [r.get("report_id") for r in expired_reports if r.get("report_id")]

        if report_ids:
            # 清理關聯的 compare_items 明細
            db.execute(
                """
                DELETE FROM compare_items 
                WHERE report_id IN (SELECT report_id FROM compare_reports WHERE created_at < ?);
                """,
                [cutoff_str]
            )
            stats["items_deleted"] = len(report_ids)

            # 清理 compare_reports 主表
            db.execute(
                "DELETE FROM compare_reports WHERE created_at < ?;",
                [cutoff_str]
            )
            stats["reports_deleted"] = len(report_ids)
            print(f"🗑️ 已清理超過 {retention_days} 天的歷史比對報告: {len(report_ids)} 筆")
        else:
            print(f"ℹ️ 無超過 {retention_days} 天的過期比對報告需清理。")

        # 2. 清理過期的各平台物件快照 (t591_houses, t5168_houses, trakuya_houses)
        try:
            db.execute("DELETE FROM t591_houses WHERE updated_at < ?;", [cutoff_str])
            print(f"🗑️ 已清理過期的 591 爬蟲快照。")
        except Exception as e:
            print(f"[Warn] 清理 t591_houses 失敗: {e}")

        try:
            db.execute("DELETE FROM t5168_houses WHERE updated_at < ?;", [cutoff_str])
            print(f"🗑️ 已清理過期的 5168 爬蟲快照。")
        except Exception as e:
            print(f"[Warn] 清理 t5168_houses 失敗: {e}")

        try:
            db.execute("DELETE FROM trakuya_houses WHERE updated_at < ?;", [cutoff_str])
            print(f"🗑️ 已清理過期的樂屋網爬蟲快照。")
        except Exception as e:
            print(f"[Warn] 清理 trakuya_houses 失敗: {e}")

        # 3. 清理已下架 (REMOVED) 或已畢業 (GRADUATED) 超過保留天數的內網回報資料
        # ⚠️ status = 'ACTIVE' 的有效在庫物業絕對保留！
        try:
            db.execute(
                """
                DELETE FROM internal_active_houses 
                WHERE status IN ('REMOVED', 'GRADUATED') AND updated_at < ?;
                """,
                [cutoff_str]
            )
            print(f"🗑️ 已清理超過 {retention_days} 天的已下架/已畢業內網回報垃圾資料。")
        except Exception as e:
            print(f"[Warn] 清理 internal_active_houses 歷史資料失敗: {e}")

        # 4. 解除超過 1 天的卡死查詢鎖定 (防呆維護)
        try:
            lock_cutoff = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("DELETE FROM user_query_locks WHERE locked_at < ?;", [lock_cutoff])
            db.execute("UPDATE system_locks SET is_locked = 0 WHERE is_locked = 1 AND locked_at < ?;", [lock_cutoff])
            print(f"🔓 已檢核並解除超時卡死的查詢鎖定。")
        except Exception as e:
            print(f"[Warn] 清理鎖定表失敗: {e}")

        print(f"================ 定期清理任務執行完畢 ================\n")

    except Exception as e:
        print(f"[Error] 定期資料清理執行失敗: {e}")

    return stats

if __name__ == "__main__":
    days = 3
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            pass
    run_cleanup(retention_days=days)
