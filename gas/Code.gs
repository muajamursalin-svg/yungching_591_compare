/**
 * Google Apps Script (GAS) - LINE Bot Webhook 與 GitHub Actions 觸發排程器
 * 支援 591 / 5168 / 樂屋網 店鋪網址自動記憶、各平台累積查詢次數統計、動態快捷按鈕與新網址更新確認
 * 
 * 部署說明：
 * 1. 在 Google Apps Script (script.google.com) 貼上本程式碼。
 * 2. 在「專案設定」->「指令碼屬性 (Script Properties)」填入以下環境變數：
 *    - LINE_CHANNEL_ACCESS_TOKEN: 您的 LINE Messaging API Channel Access Token
 *    - GITHUB_TOKEN: 您的 GitHub Personal Access Token (需有 repo 權限)
 *    - GITHUB_REPO: 您的 GitHub 倉庫路徑 (格式: "用戶名/專案名"，如 "muajamursalin-svg/yungching_591_compare")
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
    const tursoUrl = scriptProps.getProperty("TURSO_DATABASE_URL") || "https://yungchingbranchstore-steven0612.aws-ap-northeast-1.turso.io";
    const tursoToken = scriptProps.getProperty("TURSO_AUTH_TOKEN") || "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTUxNzk0MTcsImlkIjoiMDFhMWY5MDctNmQwMS03MjdkLWJkNWQtZjJmZTI3ZTE0OWExIiwia2lkIjoiUGtZTm9rMU9pRGFlVW1GNXZsVDJKbmJaTUtDSmZlZGZLZFFncFI0RlFMdyIsInJpZCI6ImQwMDA1ZmMyLTk0MTMtNDc5Yy1hZTY3LWFhNDliMGJhZDRjNCJ9.aexx6RjPmK42KGAGQP2zihQZv53w9wjkPtNdB-MMb7gA51JHJDhDEavmfeE3NSUnhD9QEYWukpod2oqeg1c2Dw";

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

/**
 * LINE Bot Webhook 事件主入口 (支援文字訊息與 Postback 點擊事件)
 */
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
    const githubRepo = scriptProps.getProperty("GITHUB_REPO") || "muajamursalin-svg/yungching_591_compare";
    const tursoUrl = scriptProps.getProperty("TURSO_DATABASE_URL") || "https://yungchingbranchstore-steven0612.aws-ap-northeast-1.turso.io";
    const tursoToken = scriptProps.getProperty("TURSO_AUTH_TOKEN") || "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTUxNzk0MTcsImlkIjoiMDFhMWY5MDctNmQwMS03MjdkLWJkNWQtZjJmZTI3ZTE0OWExIiwia2lkIjoiUGtZTm9rMU9pRGFlVW1GNXZsVDJKbmJaTUtDSmZlZGZLZFFncFI0RlFMdyIsInJpZCI6ImQwMDA1ZmMyLTk0MTMtNDc5Yy1hZTY3LWFhNDliMGJhZDRjNCJ9.aexx6RjPmK42KGAGQP2zihQZv53w9wjkPtNdB-MMb7gA51JHJDhDEavmfeE3NSUnhD9QEYWukpod2oqeg1c2Dw";
    const liffUrl = scriptProps.getProperty("LIFF_URL") || "https://muajamursalin-svg.github.io/yungching_591_compare/";

    for (let i = 0; i < events.length; i++) {
      const event = events[i];
      const replyToken = event.replyToken;
      const userId = event.source.userId;

      // ───────────────────────────────────────────────
      // A. 處理使用者點擊確認按鈕 (Postback 事件)
      // ───────────────────────────────────────────────
      if (event.type === "postback") {
        const dataStr = event.postback.data || "";
        const params = parseQueryString(dataStr);
        const action = params.action;
        const platform = params.platform;
        const shopUrl = decodeURIComponent(params.url || "");
        const shopId = params.shop_id || "";

        if (action === "update_shop") {
          // 檢查使用者當前是否已有比對任務執行中 (防止重複併發查詢)
          const userLock = checkUserQueryLock(tursoUrl, tursoToken, userId);
          if (userLock && userLock.is_locked) {
            const platName = userLock.platform === "5168" ? "5168" : (userLock.platform === "rakuya" ? "樂屋網" : "591");
            const userShops = getUserSavedShops(tursoUrl, tursoToken, userId);
            const qr = buildQuickReply(userShops);
            replyLineMessage(lineToken, replyToken,
              `⏳ 目前查詢中 請稍後\n\n` +
              `系統正為您比對【${platName}】店鋪庫存（約需 30~45 秒），完成後將自動推播報告給您。\n\n` +
              `為確保資料正確性，一次僅能查詢一個平台，請待本次比對完成後再進行下次查詢。`,
              qr
            );
            continue;
          }

          // 1. 更新資料庫並累加次數
          saveOrUpdateUserShop(tursoUrl, tursoToken, userId, platform, shopUrl, true);
          // 2. 鎖定並觸發 GitHub Actions
          acquireUserQueryLock(tursoUrl, tursoToken, userId, platform, shopId);
          const ghaOk = triggerGitHubActions(githubRepo, githubToken, shopId, shopUrl, userId, platform);
          if (!ghaOk) releaseUserQueryLock(tursoUrl, tursoToken, userId);
          // 3. 取得最新按鈕並回覆
          const latestShops = getUserSavedShops(tursoUrl, tursoToken, userId);
          const qr = buildQuickReply(latestShops);
          const count = getShopCount(latestShops, platform);

          replyLineMessage(lineToken, replyToken,
            `✅ 已為您更新 ${platform} 預設店鋪為新網址！\n\n` +
            `📍 店鋪代號: ${shopId}\n` +
            `📈 累積查詢: 第 ${count} 次\n` +
            `⏳ 系統現正同步最新庫存並進行多維特徵比對...\n\n` +
            `比對完成後將會主動發送推播報告通知您！`,
            qr
          );
          continue;

        } else if (action === "once_compare") {
          // 檢查使用者當前是否已有比對任務執行中
          const userLock = checkUserQueryLock(tursoUrl, tursoToken, userId);
          if (userLock && userLock.is_locked) {
            const platName = userLock.platform === "5168" ? "5168" : (userLock.platform === "rakuya" ? "樂屋網" : "591");
            const userShops = getUserSavedShops(tursoUrl, tursoToken, userId);
            const qr = buildQuickReply(userShops);
            replyLineMessage(lineToken, replyToken,
              `⏳ 目前查詢中 請稍後\n\n` +
              `系統正為您比對【${platName}】店鋪庫存（約需 30~45 秒），完成後將自動推播報告給您。\n\n` +
              `為確保資料正確性，一次僅能查詢一個平台，請待本次比對完成後再進行下次查詢。`,
              qr
            );
            continue;
          }

          // 僅單次比對，不更動預設資料庫
          acquireUserQueryLock(tursoUrl, tursoToken, userId, platform, shopId);
          const ghaOk = triggerGitHubActions(githubRepo, githubToken, shopId, shopUrl, userId, platform);
          if (!ghaOk) releaseUserQueryLock(tursoUrl, tursoToken, userId);
          const userShops = getUserSavedShops(tursoUrl, tursoToken, userId);
          const qr = buildQuickReply(userShops);

          replyLineMessage(lineToken, replyToken,
            `⚡ 收到！本次僅作單次比對（未變更您的預設店鋪）。\n\n` +
            `📍 ${platform} 店鋪代號: ${shopId}\n` +
            `⏳ 系統現正進行庫存比對，完成後將推播報告通知您！`,
            qr
          );
          continue;
        }
      }

      // ───────────────────────────────────────────────
      // B. 處理使用者文字訊息
      // ───────────────────────────────────────────────
      if (event.type === "message" && event.message.type === "text") {
        const userMsg = event.message.text.trim();

        // 0. 讀取使用者既有的店鋪儲存資訊
        let userShops = getUserSavedShops(tursoUrl, tursoToken, userId);

        // 1. 偵測使用者是否點擊了一鍵快捷按鈕 (例如: "查詢 591 庫存", "查詢 5168 庫存", "查詢 樂屋網 庫存")
        const isQuick591 = (userMsg === "查詢 591 庫存" || userMsg === "查 591");
        const isQuick5168 = (userMsg === "查詢 5168 庫存" || userMsg === "查 5168");
        const isQuickRakuya = (userMsg === "查詢 樂屋網 庫存" || userMsg === "查 樂屋網");

        if (isQuick591 || isQuick5168 || isQuickRakuya) {
          const reqPlatform = isQuick591 ? "591" : (isQuick5168 ? "5168" : "rakuya");
          const targetUrl = isQuick591 ? userShops?.url_591 : (isQuick5168 ? userShops?.url_5168 : userShops?.url_rakuya);

          if (!targetUrl) {
            const qr = buildQuickReply(userShops);
            replyLineMessage(lineToken, replyToken,
              `⚠️ 您尚未設定 ${reqPlatform} 的常用店鋪網址。\n\n請直接貼上您的 ${reqPlatform} 店鋪網址，系統將自動為您記憶，未來即可一鍵查詢！`,
              qr
            );
            continue;
          }

          // 遞增查詢次數
          incrementShopCount(tursoUrl, tursoToken, userId, reqPlatform);
          userShops = getUserSavedShops(tursoUrl, tursoToken, userId);
          const newCnt = getShopCount(userShops, reqPlatform);
          const qr = buildQuickReply(userShops);

          // 萃取 shopId
          const shopId = extractShopIdFromUrl(targetUrl, reqPlatform);

          // 檢查使用者當前是否已有比對任務執行中 (防止重複併發查詢)
          const userLock = checkUserQueryLock(tursoUrl, tursoToken, userId);
          if (userLock && userLock.is_locked) {
            const platName = userLock.platform === "5168" ? "5168" : (userLock.platform === "rakuya" ? "樂屋網" : "591");
            replyLineMessage(lineToken, replyToken,
              `⏳ 目前查詢中 請稍後\n\n` +
              `系統正為您比對【${platName}】店鋪庫存（約需 30~45 秒），完成後將自動推播報告給您。\n\n` +
              `為確保資料正確性，一次僅能查詢一個平台，請待本次比對完成後再進行下次查詢。`,
              qr
            );
            continue;
          }

          // 檢查永慶分店同步鎖
          const syncLock = checkBranchSyncLock(tursoUrl, tursoToken);
          if (syncLock && syncLock.is_locked) {
            replyLineMessage(lineToken, replyToken,
              `⚠️ 目前刷新分店庫存中 請五分鐘後再查詢`,
              qr
            );
            continue;
          }

          // 檢查 1 分鐘快取
          const cachedReport = checkRecentCacheFromTurso(tursoUrl, tursoToken, shopId, 60);
          if (cachedReport) {
            const targetLiff = liffUrl ? `${liffUrl}?report_id=${cachedReport.report_id}` : "";
            const summaryText = formatCompareSummaryText(
              reqPlatform,
              shopId,
              newCnt,
              cachedReport.total_591_count,
              cachedReport.matched_count,
              cachedReport.not_found_count,
              cachedReport.price_diff_count,
              cachedReport.spec_diff_count,
              true
            );

            replyLineMessage(lineToken, replyToken,
              summaryText,
              userShops,
              targetLiff
            );
            continue;
          }

          // 鎖定並觸發 GitHub Actions
          acquireUserQueryLock(tursoUrl, tursoToken, userId, reqPlatform, shopId);
          const ghaSuccess = triggerGitHubActions(githubRepo, githubToken, shopId, targetUrl, userId, reqPlatform);
          if (ghaSuccess) {
            replyLineMessage(lineToken, replyToken, 
              `✅ 收到！已啟動 ${reqPlatform} 庫存比對任務\n\n` +
              `📍 店鋪代號: ${shopId}\n` +
              `📈 累積查詢: 第 ${newCnt} 次\n` +
              `⏳ 系統正在同步 ${reqPlatform} 與永慶分店庫存並進行比對（約需 30~45 秒）...\n\n` +
              `比對完成後將會主動推播報告給您！`,
              qr
            );
          } else {
            releaseUserQueryLock(tursoUrl, tursoToken, userId);
            replyLineMessage(lineToken, replyToken, 
              `⚠️ 觸發比對排程失敗，請檢查 GitHub Token 設定。`,
              qr
            );
          }
          continue;
        }

        // 2. 偵測使用者是否貼上店鋪網址
        let platform = null;
        let shopId = null;

        const match5168 = userMsg.match(/realtor\.houseprice\.tw\/(?:agent|store)\/buy\/([0-9a-zA-Z_-]+)/i) || 
                          userMsg.match(/houseprice\.tw[^\s]*\/buy\/([0-9a-zA-Z_-]+)/i);
        const matchRakuya = userMsg.match(/vip\.rakuya\.com\.tw\/([0-9a-zA-Z_-]+)/i) ||
                            userMsg.match(/rakuya\.com\.tw[^\s]*\/([0-9a-zA-Z_-]+)/i);
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
          // 檢查使用者當前是否已有比對任務執行中 (防止重複併發查詢)
          const userLock = checkUserQueryLock(tursoUrl, tursoToken, userId);
          if (userLock && userLock.is_locked) {
            const platName = userLock.platform === "5168" ? "5168" : (userLock.platform === "rakuya" ? "樂屋網" : "591");
            const qr = buildQuickReply(userShops);
            replyLineMessage(lineToken, replyToken,
              `⏳ 目前查詢中 請稍後\n\n` +
              `系統正為您比對【${platName}】店鋪庫存（約需 30~45 秒），完成後將自動推播報告給您。\n\n` +
              `為確保資料正確性，一次僅能查詢一個平台，請待本次比對完成後再進行下次查詢。`,
              qr
            );
            continue;
          }

          // 檢查既有已存的該平台網址
          let savedUrl = "";
          if (platform === "591") savedUrl = userShops?.url_591 || "";
          else if (platform === "5168") savedUrl = userShops?.url_5168 || "";
          else if (platform === "rakuya") savedUrl = userShops?.url_rakuya || "";

          // ── 情境 C：使用者已存有網址，但這次輸入了不同網址 ──
          if (savedUrl && !isSameUrl(savedUrl, userMsg, platform)) {
            const confirmQuickReply = {
              items: [
                {
                  type: "action",
                  action: {
                    type: "postback",
                    label: "✅ 更新預設並比對",
                    data: `action=update_shop&platform=${platform}&url=${encodeURIComponent(userMsg)}&shop_id=${shopId}`,
                    displayText: "確認更新預設店鋪並執行比對"
                  }
                },
                {
                  type: "action",
                  action: {
                    type: "postback",
                    label: "⚡ 僅單次比對(不更新)",
                    data: `action=once_compare&platform=${platform}&url=${encodeURIComponent(userMsg)}&shop_id=${shopId}`,
                    displayText: "僅單次比對（不更新預設）"
                  }
                },
                {
                  type: "action",
                  action: {
                    type: "message",
                    label: "❌ 取消",
                    text: "取消查詢"
                  }
                }
              ]
            };

            replyLineMessage(lineToken, replyToken,
              `⚠️ 偵測到您輸入了新的 ${platform} 店鋪網址！\n\n` +
              `📌 原預設網址：\n${savedUrl}\n\n` +
              `🆕 新輸入網址：\n${userMsg}\n\n` +
              `請問是否要將未來的快捷查詢更新以此新網址為主？`,
              confirmQuickReply
            );
            continue;
          }

          // ── 情境 A & B：初次輸入儲存，或輸入與既有相同網址 ──
          const isFirstTime = !savedUrl;
          saveOrUpdateUserShop(tursoUrl, tursoToken, userId, platform, userMsg, true);
          userShops = getUserSavedShops(tursoUrl, tursoToken, userId);
          const currentCount = getShopCount(userShops, platform);
          const qr = buildQuickReply(userShops);

          // 檢查永慶分店庫存同步鎖
          const syncLock = checkBranchSyncLock(tursoUrl, tursoToken);
          if (syncLock && syncLock.is_locked) {
            replyLineMessage(lineToken, replyToken,
              `⚠️ 目前刷新分店庫存中 請五分鐘後再查詢`,
              qr
            );
            continue;
          }

          // 檢查 1 分鐘內快取
          const cachedReport = checkRecentCacheFromTurso(tursoUrl, tursoToken, shopId, 60);
          if (cachedReport) {
            const targetLiff = liffUrl ? `${liffUrl}?report_id=${cachedReport.report_id}` : "";
            const summaryText = formatCompareSummaryText(
              platform,
              shopId,
              currentCount,
              cachedReport.total_591_count,
              cachedReport.matched_count,
              cachedReport.not_found_count,
              cachedReport.price_diff_count,
              cachedReport.spec_diff_count,
              true
            );

            replyLineMessage(lineToken, replyToken,
              summaryText,
              userShops,
              targetLiff
            );
            continue;
          }

          // 鎖定並觸發 GitHub Actions
          acquireUserQueryLock(tursoUrl, tursoToken, userId, platform, shopId);
          const ghaSuccess = triggerGitHubActions(githubRepo, githubToken, shopId, userMsg, userId, platform);
          if (ghaSuccess) {
            const memoryTip = isFirstTime ? `💡 系統已自動為您記憶此店鋪，未來可直接點選下方按鈕一鍵查詢！\n\n` : "";
            replyLineMessage(lineToken, replyToken, 
              `✅ 收到！已成功啟動 ${platform} 庫存比對任務\n\n` +
              `📍 店鋪代號: ${shopId}\n` +
              `📈 累積查詢: 第 ${currentCount} 次\n` +
              memoryTip +
              `⏳ 系統正在同步 ${platform} 與永慶分店庫存並進行多維特徵比對（約需 30~45 秒）...\n\n` +
              `比對完成後將會主動發送推播報告通知您！`,
              qr
            );
          } else {
            releaseUserQueryLock(tursoUrl, tursoToken, userId);
            replyLineMessage(lineToken, replyToken, 
              `⚠️ 抱歉，觸發比對排程時發生錯誤，請檢查 GitHub Token 設定。`,
              qr
            );
          }
          continue;

        } else {
          // 3. 一般訊息或問候指引
          const qr = buildQuickReply(userShops);
          let extraTip = "";
          if (userShops && (userShops.url_591 || userShops.url_5168 || userShops.url_rakuya)) {
            extraTip = `\n\n✨ 點擊下方快捷按鈕，即可秒速執行常用店鋪庫存比對！`;
          }

          replyLineMessage(lineToken, replyToken, 
            `👋 您好！我是房仲跨平台庫存比對助理。${extraTip}\n\n` +
            `您也可以直接貼上店鋪網址進行比對：\n` +
            `🏠 591 店鋪：\nhttps://www.591.com.tw/broker12017\n\n` +
            `🏠 5168 房仲店鋪：\nhttps://realtor.houseprice.tw/agent/buy/0968682023/\n\n` +
            `🏠 樂屋網 VIP 店鋪：\nhttps://vip.rakuya.com.tw/0937234338/\n\n` +
            `系統將自動解析庫存，並依【縣市、區域、路名、總價、樓層、總樓高、屋齡】進行跨平台比對，標註「疑似已下架」或「價格調整」！`,
            qr
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

// ───────────────────────────────────────────────
// 資料庫輔助函式 (Turso HTTP Pipeline)
// ───────────────────────────────────────────────

/**
 * 取得使用者的已存店鋪與累積查詢次數
 */
function getUserSavedShops(tursoUrl, tursoToken, userId) {
  if (!tursoUrl || !tursoToken || !userId) return null;
  try {
    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";
    const sql = "SELECT * FROM user_saved_shops WHERE user_id = ? LIMIT 1;";
    const payload = {
      requests: [
        { type: "execute", stmt: { sql: sql, args: [{ type: "text", value: userId }] } },
        { type: "close" }
      ]
    };
    const res = UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: { "Authorization": "Bearer " + tursoToken, "Content-Type": "application/json" },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
    if (res.getResponseCode() !== 200) return null;
    const data = JSON.parse(res.getContentText());
    const result = data.results[0]?.response?.result;
    if (!result || !result.rows || result.rows.length === 0) return null;
    const cols = result.cols.map(c => c.name);
    const obj = {};
    cols.forEach((c, i) => { obj[c] = result.rows[0][i].value; });
    return obj;
  } catch (err) {
    console.warn("getUserSavedShops error: " + err);
    return null;
  }
}

/**
 * 儲存或更新使用者的店鋪網址並遞增累積次數
 */
function saveOrUpdateUserShop(tursoUrl, tursoToken, userId, platform, shopUrl, incrementCount) {
  if (!tursoUrl || !tursoToken || !userId || !platform || !shopUrl) return;
  try {
    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";
    const nowStr = Utilities.formatDate(new Date(), "GMT+8", "yyyy-MM-dd HH:mm:ss");
    
    let urlCol = "url_591";
    let countCol = "count_591";
    if (platform === "5168") {
      urlCol = "url_5168";
      countCol = "count_5168";
    } else if (platform === "rakuya") {
      urlCol = "url_rakuya";
      countCol = "count_rakuya";
    }
    
    const countInc = incrementCount ? 1 : 0;

    const sql = `
      INSERT INTO user_saved_shops (user_id, ${urlCol}, ${countCol}, updated_at)
      VALUES (?, ?, 1, ?)
      ON CONFLICT(user_id) DO UPDATE SET
        ${urlCol} = excluded.${urlCol},
        ${countCol} = COALESCE(${countCol}, 0) + ${countInc},
        updated_at = excluded.updated_at;
    `;

    const payload = {
      requests: [
        {
          type: "execute",
          stmt: {
            sql: sql,
            args: [
              { type: "text", value: userId },
              { type: "text", value: shopUrl },
              { type: "text", value: nowStr }
            ]
          }
        },
        { type: "close" }
      ]
    };

    UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: { "Authorization": "Bearer " + tursoToken, "Content-Type": "application/json" },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
  } catch (err) {
    console.warn("saveOrUpdateUserShop error: " + err);
  }
}

/**
 * 遞增指定平台的查詢次數
 */
function incrementShopCount(tursoUrl, tursoToken, userId, platform) {
  if (!tursoUrl || !tursoToken || !userId || !platform) return;
  try {
    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";
    const nowStr = Utilities.formatDate(new Date(), "GMT+8", "yyyy-MM-dd HH:mm:ss");
    let countCol = platform === "5168" ? "count_5168" : (platform === "rakuya" ? "count_rakuya" : "count_591");
    
    const sql = `UPDATE user_saved_shops SET ${countCol} = COALESCE(${countCol}, 0) + 1, updated_at = ? WHERE user_id = ?;`;
    const payload = {
      requests: [
        {
          type: "execute",
          stmt: {
            sql: sql,
            args: [
              { type: "text", value: nowStr },
              { type: "text", value: userId }
            ]
          }
        },
        { type: "close" }
      ]
    };

    UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: { "Authorization": "Bearer " + tursoToken, "Content-Type": "application/json" },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
  } catch (err) {
    console.warn("incrementShopCount error: " + err);
  }
}

/**
 * 取得指定平台的當前累積次數
 */
function getShopCount(userShops, platform) {
  if (!userShops) return 1;
  if (platform === "5168") return userShops.count_5168 || 1;
  if (platform === "rakuya") return userShops.count_rakuya || 1;
  return userShops.count_591 || 1;
}

// ───────────────────────────────────────────────
// LINE Quick Reply 動態按鈕生成
// ───────────────────────────────────────────────

/**
 * 根據使用者已綁定的平台動態產生 Quick Reply 按鈕 (只有有網址的平台才會產生對應按鈕)
 */
function buildQuickReply(userShops) {
  if (!userShops) return null;
  const items = [];

  // 1. 591
  if (userShops.url_591) {
    const cnt = userShops.count_591 || 0;
    items.push({
      type: "action",
      action: {
        type: "message",
        label: `🔍 查 591 (${cnt}次)`,
        text: userShops.url_591
      }
    });
  }

  // 2. 5168
  if (userShops.url_5168) {
    const cnt = userShops.count_5168 || 0;
    items.push({
      type: "action",
      action: {
        type: "message",
        label: `🔍 查 5168 (${cnt}次)`,
        text: userShops.url_5168
      }
    });
  }

  // 3. 樂屋網
  if (userShops.url_rakuya) {
    const cnt = userShops.count_rakuya || 0;
    items.push({
      type: "action",
      action: {
        type: "message",
        label: `🔍 查 樂屋網 (${cnt}次)`,
        text: userShops.url_rakuya
      }
    });
  }

  if (items.length === 0) return null;
  return { items: items };
}

/**
 * 從網址萃取 Shop ID
 */
function extractShopIdFromUrl(url, platform) {
  if (!url) return "";
  if (platform === "5168") {
    const m = url.match(/realtor\.houseprice\.tw\/(?:agent|store)\/buy\/([0-9a-zA-Z_-]+)/i) || 
              url.match(/houseprice\.tw[^\s]*\/buy\/([0-9a-zA-Z_-]+)/i);
    return m ? m[1] : url;
  } else if (platform === "rakuya") {
    const m = url.match(/vip\.rakuya\.com\.tw\/([0-9a-zA-Z_-]+)/i) ||
              url.match(/rakuya\.com\.tw[^\s]*\/([0-9a-zA-Z_-]+)/i);
    return m ? m[1] : url;
  } else {
    const m = url.match(/broker(\d+)/i) || 
              url.match(/(?:591\.com\.tw[^\s]*[?&]shop_id=)(\d+)/i) || 
              url.match(/^(\d{4,6})$/);
    return m ? m[1] : url;
  }
}

/**
 * 比較兩網址是否實質代表同一店鋪
 */
function isSameUrl(url1, url2, platform) {
  if (!url1 || !url2) return false;
  const id1 = extractShopIdFromUrl(url1, platform);
  const id2 = extractShopIdFromUrl(url2, platform);
  return id1 && id2 && id1.toLowerCase() === id2.toLowerCase();
}

/**
 * 解析 QueryString 為物件
 */
function parseQueryString(qs) {
  const params = {};
  if (!qs) return params;
  const pairs = qs.split("&");
  for (let i = 0; i < pairs.length; i++) {
    const pair = pairs[i].split("=");
    if (pair.length === 2) {
      params[decodeURIComponent(pair[0])] = decodeURIComponent(pair[1]);
    }
  }
  return params;
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
 * 建立互動式訊息卡片 (支援電腦端 PC 與手機端，卡片內部跟隨實體按鈕，同時支援底部 Quick Reply)
 */
function buildInteractiveMessage(text, userShops, liffUrl, customButtons) {
  const buttons = [];
  const quickItems = [];

  // 1. 若有特定情境按鈕 (例如確認更新/單次比對/取消)
  if (customButtons && customButtons.length > 0) {
    customButtons.forEach(btn => {
      buttons.push({
        type: "button",
        style: btn.style || "primary",
        color: btn.color || "#06c755",
        height: "sm",
        action: btn.action
      });
      quickItems.push({
        type: "action",
        action: btn.action
      });
    });
  } else if (userShops) {
    // 2. 依照使用者已綁定平台動態產生
    if (userShops.url_591) {
      const cnt = userShops.count_591 || 0;
      const lbl = `🔍 查 591 (${cnt}次)`;
      const txt = userShops.url_591;
      buttons.push({
        type: "button",
        style: "primary",
        color: "#ff7700",
        height: "sm",
        action: { type: "message", label: lbl, text: txt }
      });
      quickItems.push({
        type: "action",
        action: { type: "message", label: lbl, text: txt }
      });
    }

    if (userShops.url_5168) {
      const cnt = userShops.count_5168 || 0;
      const lbl = `🔍 查 5168 (${cnt}次)`;
      const txt = userShops.url_5168;
      buttons.push({
        type: "button",
        style: "primary",
        color: "#0084ff",
        height: "sm",
        action: { type: "message", label: lbl, text: txt }
      });
      quickItems.push({
        type: "action",
        action: { type: "message", label: lbl, text: txt }
      });
    }

    if (userShops.url_rakuya) {
      const cnt = userShops.count_rakuya || 0;
      const lbl = `🔍 查 樂屋網 (${cnt}次)`;
      const txt = userShops.url_rakuya;
      buttons.push({
        type: "button",
        style: "primary",
        color: "#e60012",
        height: "sm",
        action: { type: "message", label: lbl, text: txt }
      });
      quickItems.push({
        type: "action",
        action: { type: "message", label: lbl, text: txt }
      });
    }

    if (liffUrl) {
      buttons.push({
        type: "button",
        style: "secondary",
        height: "sm",
        action: {
          type: "uri",
          label: "📊 開啟 LIFF 儀表板",
          uri: liffUrl
        }
      });
    }
  }

  // 若完全沒有任何按鈕，退回一般文字訊息
  if (buttons.length === 0) {
    return { type: "text", text: text };
  }

  // 決定 Header 橫幅：依使用者需求，只有庫存比對完成且有帶入 LIFF 儀表板時，最上方才顯示綠色 Header
  let headerColor = null;
  let headerTitle = null;

  if (liffUrl && (text.indexOf("庫存比對結果總覽") !== -1 || text.indexOf("比對報告") !== -1)) {
    if (text.indexOf("快取") !== -1) {
      headerColor = "#00b900";
      headerTitle = "⚡ 庫存比對完成（快取紀錄 ✔）";
    } else {
      headerColor = "#06C755";
      headerTitle = "✅ 庫存比對完成 ✔";
    }
  }

  // 摘要標題用於推播與無障礙視窗
  const altText = headerTitle || (text.split("\n")[0] || "比對助理回覆").substring(0, 38);

  const bubbleContents = {
    type: "bubble",
    body: {
      type: "box",
      layout: "vertical",
      contents: [
        {
          type: "text",
          text: text,
          size: "sm",
          wrap: true,
          lineSpacing: "5px"
        }
      ]
    },
    footer: {
      type: "box",
      layout: "vertical",
      spacing: "sm",
      contents: buttons
    }
  };

  if (headerColor && headerTitle) {
    bubbleContents.header = {
      type: "box",
      layout: "vertical",
      backgroundColor: headerColor,
      paddingTop: "14px",
      paddingBottom: "14px",
      contents: [
        {
          type: "text",
          text: headerTitle,
          weight: "bold",
          size: "md",
          color: "#ffffff"
        }
      ]
    };
  }

  const flexMsg = {
    type: "flex",
    altText: altText,
    contents: bubbleContents
  };

  if (quickItems.length > 0) {
    flexMsg.quickReply = { items: quickItems };
  }

  return flexMsg;
}

/**
 * 產生包含打勾符號與結構化排版的比對摘要文字
 */
function formatCompareSummaryText(platform, shopId, currentCount, total, matched, notFound, priceDiff, specDiff, isCache) {
  const platDisplay = platform === "5168" ? "5168 房仲店鋪" : (platform === "rakuya" ? "樂屋網 VIP 店鋪" : "591 店鋪");
  const abnormal = (parseInt(notFound) || 0) + (parseInt(priceDiff) || 0) + (parseInt(specDiff) || 0);
  const cacheNote = isCache ? "（距前次比對未滿 1 分鐘，回傳最新快取紀錄）\n\n" : "";

  let abnormalDetail = "";
  let statusNote = "";
  if (abnormal === 0) {
    abnormalDetail = "  🎉 太棒了！全部物件皆與分店一致，未發現任何異常 ✔";
    statusNote = "🎉 全數正常 ✔";
  } else {
    statusNote = `⚠️ 發現 ${abnormal} 件異常`;
    abnormalDetail = 
      `  • ❌ 查無分店庫存：${notFound} 件 (疑似已下架)\n` +
      `  • 💰 總價標價出入：${priceDiff} 件 (開價不一致)\n` +
      `  • 📐 規格不符提示：${specDiff || 0} 件 (樓層/規格出入)`;
  }

  const countStr = currentCount ? `📈 累積查詢：第 ${currentCount} 次\n` : "";

  return (
    `${cacheNote}` +
    `🏢 比對平台：${platDisplay}\n` +
    `📍 店鋪代號：${shopId}\n` +
    countStr +
    `📦 在庫總數：${total} 件\n\n` +
    `【📊 庫存比對結果總覽】\n` +
    `✔ 🟢 正常在售：${matched} 件 (與分店庫存相符)\n` +
    `❗ ⚠️ 異常物件：${abnormal} 件 (${statusNote})\n` +
    `━━━━━━━━━━━━━━━━\n` +
    `【🔍 異常類別明細】\n` +
    `${abnormalDetail}\n` +
    `━━━━━━━━━━━━━━━━\n` +
    `💡 點擊下方【📊 開啟 LIFF 儀表板】可查看完整物件診斷與下架標註！`
  );
}

/**
 * LINE Messaging API 回覆訊息 (全面支援 Flex 訊息卡片 + 實體按鈕 + 底部 Quick Reply)
 */
function replyLineMessage(token, replyToken, text, userShops, liffUrl, customButtons) {
  if (!token) return;
  const url = "https://api.line.me/v2/bot/message/reply";

  // 若傳入的是包含 items 的 Quick Reply 物件，自動轉為實體按鈕 (自帶平台專屬色彩)
  if (userShops && userShops.items && Array.isArray(userShops.items)) {
    customButtons = userShops.items.map(item => {
      let color = undefined;
      let style = "primary";
      const lbl = item.action?.label || "";
      const dataStr = item.action?.data || "";
      if (lbl.indexOf("591") !== -1) {
        color = "#ff7700";
      } else if (lbl.indexOf("5168") !== -1) {
        color = "#0084ff";
      } else if (lbl.indexOf("樂屋網") !== -1) {
        color = "#e60012";
      } else if (dataStr.indexOf("update_shop") !== -1) {
        color = "#06c755";
      } else if (dataStr.indexOf("once_compare") !== -1) {
        color = "#3b82f6";
      } else {
        style = "secondary";
      }
      return {
        style: style,
        color: color,
        action: item.action
      };
    });
    userShops = null;
  }

  const msgObj = buildInteractiveMessage(text, userShops, liffUrl, customButtons);

  const payload = {
    replyToken: replyToken,
    messages: [msgObj]
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

  const res = UrlFetchApp.fetch(url, options);
  console.log("LINE Reply Status: " + res.getResponseCode());
}

// ───────────────────────────────────────────────
// 使用者查詢鎖定控制 (防止重複併發查詢)
// ───────────────────────────────────────────────

/**
 * 檢查使用者當前是否已有正在執行的比對任務
 */
function checkUserQueryLock(tursoUrl, tursoToken, userId) {
  if (!tursoUrl || !tursoToken || !userId) return null;
  try {
    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";
    const sql = "SELECT user_id, is_running, platform, shop_id, locked_at FROM user_query_locks WHERE user_id = ? LIMIT 1;";
    const payload = {
      requests: [
        { type: "execute", stmt: { sql: sql, args: [{ type: "text", value: userId }] } },
        { type: "close" }
      ]
    };
    const res = UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: { "Authorization": "Bearer " + tursoToken, "Content-Type": "application/json" },
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
    cols.forEach((col, idx) => { lockObj[col] = row[idx].value; });

    if (parseInt(lockObj.is_running) === 1) {
      // 逾時防護：若超過 180 秒 (3 分鐘) 視為逾時自動解鎖，避免意外中斷產生死鎖
      if (lockObj.locked_at) {
        const lockedTime = new Date(lockObj.locked_at.replace(" ", "T") + "+08:00").getTime();
        const now = new Date().getTime();
        const diffSec = (now - lockedTime) / 1000;
        if (diffSec > 180) {
          console.warn("User query lock expired (>180s), ignoring.");
          return null;
        }
      }
      return {
        is_locked: true,
        platform: lockObj.platform || "",
        shop_id: lockObj.shop_id || ""
      };
    }
  } catch (err) {
    console.warn("checkUserQueryLock failed: " + err);
  }
  return null;
}

/**
 * 取得使用者比對任務鎖定 (標記 is_running = 1)
 */
function acquireUserQueryLock(tursoUrl, tursoToken, userId, platform, shopId) {
  if (!tursoUrl || !tursoToken || !userId) return;
  try {
    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";
    const nowStr = Utilities.formatDate(new Date(), "GMT+8", "yyyy-MM-dd HH:mm:ss");
    const sql = `
      INSERT INTO user_query_locks (user_id, is_running, platform, shop_id, locked_at)
      VALUES (?, 1, ?, ?, ?)
      ON CONFLICT(user_id) DO UPDATE SET
        is_running = 1,
        platform = excluded.platform,
        shop_id = excluded.shop_id,
        locked_at = excluded.locked_at;
    `;
    const payload = {
      requests: [
        {
          type: "execute",
          stmt: {
            sql: sql,
            args: [
              { type: "text", value: userId },
              { type: "text", value: platform || "" },
              { type: "text", value: shopId ? shopId.toString() : "" },
              { type: "text", value: nowStr }
            ]
          }
        },
        { type: "close" }
      ]
    };
    UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: { "Authorization": "Bearer " + tursoToken, "Content-Type": "application/json" },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
  } catch (err) {
    console.warn("acquireUserQueryLock failed: " + err);
  }
}

/**
 * 釋放使用者比對任務鎖定 (標記 is_running = 0)
 */
function releaseUserQueryLock(tursoUrl, tursoToken, userId) {
  if (!tursoUrl || !tursoToken || !userId) return;
  try {
    const pipelineUrl = tursoUrl.replace("libsql://", "https://").replace(/\/$/, "") + "/v2/pipeline";
    const sql = "UPDATE user_query_locks SET is_running = 0 WHERE user_id = ?;";
    const payload = {
      requests: [
        {
          type: "execute",
          stmt: {
            sql: sql,
            args: [{ type: "text", value: userId }]
          }
        },
        { type: "close" }
      ]
    };
    UrlFetchApp.fetch(pipelineUrl, {
      method: "post",
      headers: { "Authorization": "Bearer " + tursoToken, "Content-Type": "application/json" },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
  } catch (err) {
    console.warn("releaseUserQueryLock failed: " + err);
  }
}
