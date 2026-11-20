/**
 * Google Apps Script (GAS) - LINE Bot Webhook 與 GitHub Actions 觸發排程器
 * 
 * 部署說明：
 * 1. 在 Google Apps Script (script.google.com) 建立專案。
 * 2. 在「專案設定」->「指令碼屬性 (Script Properties)」填入以下環境變數：
 *    - LINE_CHANNEL_ACCESS_TOKEN: 您的 LINE Messaging API Channel Access Token
 *    - GITHUB_TOKEN: 您的 GitHub Personal Access Token (需有 repo 權限)
 *    - GITHUB_REPO: 您的 GitHub 倉庫路徑 (格式: "用戶名/專案名"，如 "steven0612/house-inventory")
 *    - TURSO_DATABASE_URL: 您的 Turso URL (如 https://yungchingbranchstore-steven0612.aws-ap-northeast-1.turso.io)
 *    - TURSO_AUTH_TOKEN: 您的 Turso Token
 *    - LIFF_URL: 您的 GitHub Pages LIFF 網址
 * 3. 部署為「網頁應用程式 (Web App)」，存取權限設為「任何人 (Anyone)」。
 * 4. 將取得的 Web App URL 填入 LINE Developers Console 的 Webhook URL 並開啟 Use Webhook。
 */

/**
 * 提供 LIFF 前端透過 HTTP GET 安全讀取比對報告 (無需將 Turso Token 暴露於前端)
 */
function doGet(e) {
  try {
    const action = e.parameter.action;
    const reportId = e.parameter.report_id;
    const shopId = e.parameter.shop_id || "12017";

    const scriptProps = PropertiesService.getScriptProperties();
    const tursoUrl = scriptProps.getProperty("TURSO_DATABASE_URL");
    const tursoToken = scriptProps.getProperty("TURSO_AUTH_TOKEN");

    if (!tursoUrl || !tursoToken) {
      return ContentService.createTextOutput(JSON.stringify({ error: "Missing Turso config" })).setMimeType(ContentService.MimeType.JSON);
    }

    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";

    // 1. 查詢主報告
    let rptSql = "SELECT * FROM compare_reports ";
    let rptArgs = [];
    if (reportId) {
      rptSql += "WHERE report_id = ? LIMIT 1;";
      rptArgs.push({ type: "text", value: reportId });
    } else {
      rptSql += "WHERE shop_id = ? ORDER BY created_at DESC LIMIT 1;";
      rptArgs.push({ type: "text", value: shopId });
    }

    const res1 = UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: { "Authorization": "Bearer " + tursoToken, "Content-Type": "application/json" },
      payload: JSON.stringify({ requests: [{ type: "execute", stmt: { sql: rptSql, args: rptArgs } }, { type: "close" }] }),
      muteHttpExceptions: true
    });

    const data1 = JSON.parse(res1.getContentText());
    const rptResult = data1.results[0]?.response?.result;
    if (!rptResult || !rptResult.rows || rptResult.rows.length === 0) {
      return ContentService.createTextOutput(JSON.stringify({ error: "Report not found" })).setMimeType(ContentService.MimeType.JSON);
    }

    const rptCols = rptResult.cols.map(c => c.name);
    const reportObj = {};
    rptCols.forEach((col, idx) => { reportObj[col] = rptResult.rows[0][idx].value; });

    // 2. 查詢報告明細項
    const itemSql = "SELECT * FROM compare_items WHERE report_id = ? ORDER BY status, id;";
    const res2 = UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: { "Authorization": "Bearer " + tursoToken, "Content-Type": "application/json" },
      payload: JSON.stringify({ requests: [{ type: "execute", stmt: { sql: itemSql, args: [{ type: "text", value: reportObj.report_id }] } }, { type: "close" }] }),
      muteHttpExceptions: true
    });

    const data2 = JSON.parse(res2.getContentText());
    const itemResult = data2.results[0]?.response?.result;
    const items = [];
    if (itemResult && itemResult.rows) {
      const itemCols = itemResult.cols.map(c => c.name);
      itemResult.rows.forEach(r => {
        const itemObj = {};
        itemCols.forEach((col, idx) => { itemObj[col] = r[idx].value; });
        items.push(itemObj);
      });
    }

    const output = {
      status: "success",
      report: reportObj,
      items: items
    };

    return ContentService.createTextOutput(JSON.stringify(output)).setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ error: err.toString() })).setMimeType(ContentService.MimeType.JSON);
  }
}

function doPost(e) {
  try {
    const postData = JSON.parse(e.postData.contents);
    const events = postData.events;
    if (!events || events.length === 0) {
      return ContentService.createTextOutput("OK");
    }

    const scriptProps = PropertiesService.getScriptProperties();
    const lineToken = scriptProps.getProperty("LINE_CHANNEL_ACCESS_TOKEN");
    const githubToken = scriptProps.getProperty("GITHUB_TOKEN");
    const githubRepo = scriptProps.getProperty("GITHUB_REPO");
    const tursoUrl = scriptProps.getProperty("TURSO_DATABASE_URL");
    const tursoToken = scriptProps.getProperty("TURSO_AUTH_TOKEN");
    const liffUrl = scriptProps.getProperty("LIFF_URL") || "";

    for (let i = 0; i < events.length; i++) {
      const event = events[i];
      if (event.type === "message" && event.message.type === "text") {
        const userMsg = event.message.text.trim();
        const replyToken = event.replyToken;
        const userId = event.source.userId;

        let platform = null;
        let shopId = null;

        // 1. 偵測 5168 房仲個人店鋪或加盟店網址
        const match5168 = userMsg.match(/realtor\.houseprice\.tw\/(?:agent|store)\/buy\/([0-9a-zA-Z_-]+)/i) || 
                          userMsg.match(/houseprice\.tw[^\s]*\/buy\/([0-9a-zA-Z_-]+)/i);
        
        // 2. 偵測 樂屋網 VIP 店鋪網址
        const matchRakuya = userMsg.match(/vip\.rakuya\.com\.tw\/([0-9a-zA-Z_-]+)/i) ||
                            userMsg.match(/rakuya\.com\.tw[^\s]*\/([0-9a-zA-Z_-]+)/i);

        // 3. 偵測 591 店鋪網址或純代號
        const match591 = userMsg.match(/broker(\d+)/i) || 
                         userMsg.match(/(?:591\.com\.tw[^\s]*[?&]shop_id=)(\d+)/i) || 
                         userMsg.match(/^(\d{4,6})$/);

        if (match5168) {
          platform = "5168";
          shopId = match5168[1];
        } else if (matchRakuya) {
          platform = "rakuya";
          shopId = matchRakuya[1];
        } else if (match591) {
          platform = "591";
          shopId = match591[1];
        }

        if (platform && shopId) {
          // 0. 檢查永慶分店庫存是否正在每六小時刷新中 (鎖定狀態)
          const syncLock = checkBranchSyncLock(tursoUrl, tursoToken);
          if (syncLock && syncLock.is_locked) {
            replyLineMessage(lineToken, replyToken,
              `⚠️ 目前刷新分店庫存中 請五分鐘後再查詢`
            );
            continue;
          }

          // 1. 檢查 1 分鐘內快取 (若距上次查詢小於 60 秒，直接秒回報告)
          const cachedReport = checkRecentCacheFromTurso(tursoUrl, tursoToken, shopId, 60);

          if (cachedReport) {
            const abnormal = (parseInt(cachedReport.not_found_count) || 0) + 
                             (parseInt(cachedReport.price_diff_count) || 0) + 
                             (parseInt(cachedReport.spec_diff_count) || 0);
            const targetLiff = liffUrl ? `${liffUrl}?report_id=${cachedReport.report_id}` : "";

            replyLineMessage(lineToken, replyToken,
              `⚡【${platform} 店鋪比對報告（快取紀錄）】\n` +
              `（系統偵測 1 分鐘內已有比對記錄，直接回傳最新結果）\n\n` +
              `📍 ${platform} 店鋪代號: ${shopId}\n` +
              `📦 ${platform} 總在售: ${cachedReport.total_591_count} 件\n` +
              `🟢 正常在售: ${cachedReport.matched_count} 件\n` +
              `⚠️ 異常物件: ${abnormal} 件\n` +
              `  • 查無分店庫存 (疑似下架): ${cachedReport.not_found_count} 件\n` +
              `  • 總價標價不一致: ${cachedReport.price_diff_count} 件\n` +
              `  • 規格/樓層/屋齡出入: ${cachedReport.spec_diff_count || 0} 件\n\n` +
              (targetLiff ? `📊 點擊查看 LIFF 儀表板：\n${targetLiff}` : "")
            );
            continue;
          }

          // 2. 超過 1 分鐘，觸發 GitHub Actions 執行最新比對
          const ghaSuccess = triggerGitHubActions(githubRepo, githubToken, shopId, userMsg, userId, platform);

          if (ghaSuccess) {
            replyLineMessage(lineToken, replyToken, 
              `✅ 收到！已成功啟動 ${platform} 庫存比對任務\n\n` +
              `📍 ${platform} 店鋪代號: ${shopId}\n` +
              `⏳ 系統正在同步 ${platform} 與永慶分店庫存並進行多維特徵比對（約需 30~45 秒）...\n\n` +
              `比對完成後，系統將會主動發送推播報告通知您！`
            );
          } else {
            replyLineMessage(lineToken, replyToken, 
              `⚠️ 抱歉，觸發比對排程時發生錯誤，請檢查 GitHub Token 設定。`
            );
          }
        } else {
          // 非支援網址時的回應指引
          replyLineMessage(lineToken, replyToken, 
            `👋 您好！我是房仲跨平台庫存比對助理。\n\n` +
            `請直接貼上您的店鋪網址，例如：\n` +
            `🏠 591 店鋪：\nhttps://www.591.com.tw/broker12017\n\n` +
            `🏠 5168 房仲店鋪：\nhttps://realtor.houseprice.tw/agent/buy/0968682023/\n\n` +
            `🏠 樂屋網 VIP 店鋪：\nhttps://vip.rakuya.com.tw/0937234338/\n\n` +
            `系統將自動解析庫存，並依【縣市、區域、路名、總價、樓層、總樓高、屋齡】進行跨平台比對，標註「疑似已下架」或「價格調整」！`
          );
        }
      }
    }

    return ContentService.createTextOutput("OK");
  } catch (err) {
    console.error("doPost Error: " + err.toString());
    return ContentService.createTextOutput("Error: " + err.toString());
  }
}

/**
 * 檢查 Turso system_locks 中永慶分店庫存是否正在刷新中
 */
function checkBranchSyncLock(tursoUrl, tursoToken) {
  if (!tursoUrl || !tursoToken) return null;

  try {
    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";
    const sql = "SELECT lock_key, is_locked, locked_at, message FROM system_locks WHERE lock_key = 'branch_sync' LIMIT 1;";
    const payload = {
      requests: [
        {
          type: "execute",
          stmt: { sql: sql, args: [] }
        },
        { type: "close" }
      ]
    };

    const res = UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: {
        "Authorization": "Bearer " + tursoToken,
        "Content-Type": "application/json"
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });

    if (res.getResponseCode() !== 200) return null;

    const data = JSON.parse(res.getContentText());
    const result = data.results[0]?.response?.result;
    if (!result || !result.rows || result.rows.length === 0) return null;

    const cols = result.cols.map(c => c.name);
    const row = result.rows[0];
    const lockObj = {};
    cols.forEach((col, idx) => {
      lockObj[col] = row[idx].value;
    });

    if (parseInt(lockObj.is_locked) === 1) {
      // 檢查是否超過 15 分鐘 (900秒)，避免異常中斷產生的死鎖
      if (lockObj.locked_at) {
        const lockedTime = new Date(lockObj.locked_at.replace(" ", "T") + "+08:00").getTime();
        const now = new Date().getTime();
        const diffSec = (now - lockedTime) / 1000;
        if (diffSec > 900) {
          console.warn("Sync lock expired (>15m), ignoring.");
          return null;
        }
      }
      return {
        is_locked: true,
        message: lockObj.message || "目前刷新分店庫存中 請五分鐘後再查詢"
      };
    }
  } catch (err) {
    console.warn("checkBranchSyncLock failed: " + err);
  }
  return null;
}

/**
 * 直接透過 Turso HTTP API 查詢 1 分鐘內的既有比對報告
 */
function checkRecentCacheFromTurso(tursoUrl, tursoToken, shopId, maxSeconds) {
  if (!tursoUrl || !tursoToken) return null;

  try {
    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";
    const sql = "SELECT * FROM compare_reports WHERE shop_id = ? ORDER BY created_at DESC LIMIT 1;";
    const payload = {
      requests: [
        {
          type: "execute",
          stmt: {
            sql: sql,
            args: [{ type: "text", value: shopId.toString() }]
          }
        },
        { type: "close" }
      ]
    };

    const res = UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: {
        "Authorization": "Bearer " + tursoToken,
        "Content-Type": "application/json"
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });

    if (res.getResponseCode() !== 200) return null;

    const data = JSON.parse(res.getContentText());
    const result = data.results[0]?.response?.result;
    if (!result || !result.rows || result.rows.length === 0) return null;

    const cols = result.cols.map(c => c.name);
    const row = result.rows[0];
    const rpt = {};
    cols.forEach((col, idx) => {
      rpt[col] = row[idx].value;
    });

    // 檢查時間差
    if (rpt.created_at) {
      const createdTime = new Date(rpt.created_at.replace(" ", "T") + "+08:00").getTime();
      const now = new Date().getTime();
      const diffSec = (now - createdTime) / 1000;
      if (diffSec >= 0 && diffSec <= maxSeconds) {
        return rpt;
      }
    }
  } catch (err) {
    console.warn("Turso cache check failed: " + err);
  }
  return null;
}

/**
 * 觸發 GitHub Actions repository_dispatch 事件
 */
function triggerGitHubActions(repo, token, shopId, shopUrl, userId, platform) {
  if (!repo || !token) {
    console.error("缺少 GITHUB_REPO 或 GITHUB_TOKEN 設定");
    return false;
  }

  const url = `https://api.github.com/repos/${repo}/dispatches`;
  const eventType = platform === "5168" ? "compare_5168" : (platform === "rakuya" ? "compare_rakuya" : "compare_591");
  const payload = {
    event_type: eventType,
    client_payload: {
      platform: platform || "591",
      shop_id: shopId,
      shop_url: shopUrl,
      user_id: userId
    }
  };

  const options = {
    method: "post",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Accept": "application/vnd.github.v3+json",
      "User-Agent": "Google-Apps-Script-LineBot"
    },
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  const response = UrlFetchApp.fetch(url, options);
  const code = response.getResponseCode();
  console.log(`GitHub Dispatch Response: ${code}`);
  return code === 204 || code === 200;
}

/**
 * LINE Messaging API 回覆訊息
 */
function replyLineMessage(token, replyToken, text) {
  if (!token) return;
  const url = "https://api.line.me/v2/bot/message/reply";
  const payload = {
    replyToken: replyToken,
    messages: [
      { type: "text", text: text }
    ]
  };

  const options = {
    method: "post",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json"
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  UrlFetchApp.fetch(url, options);
}
