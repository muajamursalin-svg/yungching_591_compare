# 591 與永慶分店庫存自動比對系統

本專案整合 **LINE Bot、Google Apps Script (GAS)、GitHub Actions、Turso 雲端 SQLite 資料庫 與 GitHub Pages (LIFF 前端)**，實現房仲跨平台庫存自動比對與即時異常監控。

---

## 系統核心功能

1. **永慶分店自動同步**：
   * 定時透過底層 JSON API 抓取 `branches.json` 中所有分店的在庫物件並寫入 Turso 雲端資料庫。
   * 未來新增分店只需在 `branches.json` 加入店號即可自動全量同步。
2. **591 店鋪比對引擎**：
   * 使用者在 LINE 輸入 591 店鋪網址（如 `https://www.591.com.tw/broker12017`）。
   * 透過 591 底層 API 抓取在售庫存，並與永慶分店庫存進行多維特徵模糊比對（社區名稱、登記坪數、所在樓層、房數、路街名）。
   * 自動標註：
     * 🔴 **疑似已下架**：分店查無此庫存。
     * 🟡 **價格調整**：物件匹配但兩邊標價不同（精確標記差額）。
     * 🟢 **正常在售**：在庫中且標價一致。
3. **LIFF 視覺化儀表板**：
   * 部署於 GitHub Pages，支援手機 LINE 內以原生體驗查看總在售量、異常件數、分類標籤切換與問題物件詳細原因。

---

## 專案目錄結構

```text
├── .github/workflows/
│   ├── sync_branches.yml       # GitHub Actions: 定時排程同步永慶分店庫存
│   └── compare_591.yml         # GitHub Actions: 接收 LINE 觸發執行 591 爬蟲與比對
├── docs/
│   └── index.html              # GitHub Pages / LIFF 前端視覺化儀表板
├── gas/
│   └── Code.gs                 # Google Apps Script: LINE Bot Webhook 與 GitHub 觸發器
├── branches.json               # 永慶分店設定清單 (可隨時擴充分店)
├── turso_client.py             # Turso 雲端 Serverless SQLite HTTP 驅動客戶端
├── sync_branches.py            # 永慶分店庫存同步主程式
├── crawler_591.py              # 591 店鋪物件抓取主程式
├── comparator.py               # 跨平台多維特徵比對演算法
├── run_compare_task.py         # 比對流程協調器 (包含完成後的 LINE 推播通知)
├── schema.sql                  # Turso 資料庫 Schema DDL
├── 591_api.md                  # 591 底層 API 技術文件
└── yungching_api.md            # 永慶底層 API 技術文件
```

---

## 部署與設定指引

### 步驟 1：GitHub 倉庫 Secrets 設定
在您的 GitHub 專案頁面點擊 **Settings** -> **Secrets and variables** -> **Actions**，新增以下 Repository Secrets：

| Secret 名稱 | 內容說明 |
| :--- | :--- |
| `TURSO_DATABASE_URL` | Turso 資料庫網址（如 `libsql://yungchingbranchstore-steven0612.aws-ap-northeast-1.turso.io`） |
| `TURSO_AUTH_TOKEN` | Turso Auth Token（`eyJhbGci...`） |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging API Channel Access Token（用於完成時推播通知） |
| `LIFF_URL` | GitHub Pages / LIFF 網址（如 `https://您的用戶名.github.io/專案名/`） |

---

### 步驟 2：開啟 GitHub Pages (LIFF 前端)
1. 前往 GitHub 倉庫的 **Settings** -> **Pages**。
2. 在 **Branch** 選擇 `main`，資料夾選擇 `/docs`，點擊 **Save**。
3. 發布後取得的網址即可作為 LINE LIFF Endpoint URL。

---

### 步驟 3：部署 Google Apps Script (LINE Bot Webhook)
1. 前往 [script.google.com](https://script.google.com) 建立新專案。
2. 將 `gas/Code.gs` 的內容複製並貼上。
3. 在專案設定的 **指令碼屬性 (Script Properties)** 加入：
   * `LINE_CHANNEL_ACCESS_TOKEN`: LINE Channel Access Token
   * `GITHUB_TOKEN`: GitHub Personal Access Token (PAT，需勾選 `repo` 權限)
   * `GITHUB_REPO`: `您的GitHub帳號/專案名稱` (例如 `steven0612/house-inventory`)
4. 點擊 **部署** -> **新增部署作業** -> 選擇 **網頁應用程式**：
   * 誰可以存取：選擇 **任何人 (Anyone)**。
5. 將生成的 Web App URL 貼到 **LINE Developers Console** 的 **Webhook URL** 並開啟 **Use Webhook**。

---

### 步驟 4：新增/擴充永慶分店
未來若要納入更多永慶房產集團的分店，只需在 `branches.json` 增加分店代碼即可：
```json
[
  {
    "branch_id": "0424522080",
    "name": "永義房屋 台中西屯溱富加盟店",
    "brand": "永義房屋",
    "enabled": true
  },
  {
    "branch_id": "0424522081",
    "name": "永慶不動產 某某加盟店",
    "brand": "永慶不動產",
    "enabled": true
  }
]
```
排程會自動遍歷並全量抓取所有分店在庫物件。
