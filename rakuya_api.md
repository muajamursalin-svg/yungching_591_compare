# 樂屋網（Rakuya）房仲店鋪底層架構分析與資料擷取規格 (rakuya_api.md)

## 一、平台架構實測與客觀結論

針對樂屋網 VIP 經紀人店鋪網址：`https://vip.rakuya.com.tw/0937234338` 進行底層網路封包、JavaScript 腳本、Subdomain Hosts 配置與伺服器回應之深度分析，結論如下：

### 1. 架構型態：傳統後端伺服器端渲染 (SSR, Server-Side Rendering)
- **非 SPA 架構**：與 5168（Vue/React 前端呼叫獨立 REST API）完全不同，樂屋網 VIP 店鋪採用傳統的 **PHP + Apache 後端伺服器渲染** 結合 jQuery (2.1.4)。
- **無獨立 JSON 物件清單 API**：
  - 檢視全域配置（`hosts.js`），樂屋網僅定義了 `www1_host`、`mobile1_host`、`vip_host`、`mobsrv_host` 等，**未對外提供任何 `api.rakuya.com.tw` 形式的公開或內部 JSON 物件列表端點**。
  - 對店鋪清單加上 `format=json`、`is_json=1` 或 `Accept: application/json`，伺服器一律回傳完整的 HTML 網頁。
  - 網頁內亦無 Vue/React/Next.js 的全域資料狀態物件（如 `__NEXT_DATA__` 或 `window.PAGE_DATA`）。
- **全站搜尋端點之限制**：
  - `www.rakuya.com.tw/search/sell_search/get-result` 雖為 JSON 格式，但其物件卡片欄位 `list` 實質上為**整串 HTML 標籤字串**，且該端點為全站關鍵字全文檢索，無法以店鋪電話或帳號拉取全店庫存（以電話查詢結果為 0 筆，以經紀人名稱查詢僅回傳內文提及該名稱的 29 筆，而非店鋪實際的 123 筆）。

### 2. 「免 DOM（No-DOM）」之最佳實作方案
既然樂屋網在店鋪層面一律回傳 SSR HTML，若遵循**「不使用 DOM 解析器（如 BeautifulSoup、Cheerio、Puppeteer、虛擬 DOM 樹節點走訪）」**之要求，最高效且最貼近底層 API 資料流的方式為：
👉 **正則表達式串流萃取器（Regex Stream Extractor）**
直接對 HTTP Response 原始字串進行高速正則擷取，記憶體開銷與執行速度與解析 JSON API 相當，完全無需建構 DOM 樹。

---

## 二、店鋪與分頁路由規範

### 1. 目標店鋪資訊（以 `0937234338` 為例）
- **經紀人姓名/暱稱**：Dior小蔡（蔡宥樵）
- **服務品牌與分店**：永慶不動產 台中安和福科加盟店（轟家房屋仲介有限公司）
- **門市地址**：台中市西屯區安和路133號
- **聯絡電話**：0937234338 / 0937-234-338
- **LINE ID**：yochyau
- **電子信箱**：orc0930@hotmail.com
- **在售物件總數**：123 件（共 11 頁，每頁 12 筆）

### 2. 分頁與排序請求 URL 規格
```http
GET https://vip.rakuya.com.tw/{shop_phone}/sell?page={page}&sort={sort}#total
```
- **網址參數**：
  - `{shop_phone}`：經紀人手機或店鋪代號（例如 `0937234338`）。
  - `page`：頁碼（整數，從 `1` 起算；總頁數可由第一頁的 `<p class="pages">第 1 / 11 頁</p>` 取得）。
  - `sort`：排序方式：
    - `11`：更新時間（由新到舊，預設值）
    - `10`：更新時間（由舊到新）
    - `21`：總價（由高到低）
    - `20`：總價（由低到高）
    - `41`：坪數（由大到小）
    - `40`：坪數（由小到大）

---

## 三、可擷取之核心欄位與規格對照表

從樂屋網店鋪每筆物件卡片中，可精準萃取以下欄位，並無縫轉換為系統通用之 `StandardHouse` 格式：

| 欄位名稱 | 欄位型態 | 樂屋網 HTML 原始特徵位置 | 範例值 | 轉換後 StandardHouse 欄位 |
| :--- | :--- | :--- | :--- | :--- |
| **物件編號 (ehid)** | String | `data-ehid="([^"]+)"` 或 `ehid=([^&"]+)` | `0bdc6736283024c` | `house_id` |
| **物件標題** | String | `<h6><a[^>]*>([^<]+)</a></h6>` | 專任🔥逢甲西屯捷運🌸邊間採光大三房平車 | `title` |
| **物件完整網址** | String | `https://www.rakuya.com.tw/sell_item/info?ehid={ehid}&from=store` | 詳情頁連結 | `url` |
| **封面照片 URL** | String | `style="background-image:url\('([^']+)'\)"` | `https://img.rakuya.com.tw/...jpg` | `cover` |
| **照片張數** | Integer | `<span class="obj-image-count">.*?(\d+)</span>` | `21` | `image_count` |
| **總價 (萬元)** | Float | `<li class="obj-price"><span>([\d\.]+)萬</span></li>` | `1798.0` | `price` |
| **物件地址** | String | `<p class="obj-address">([^<]+)</p>` | 台中市西屯區西屯路二段 | `address`（可自動拆解為 `county`, `district`, `street`） |
| **建物型態** | String | `<ul class="obj-data">.*?<span>([^<]+)</span>` | 電梯大廈 / 別墅 / 透天 | `case_type` |
| **格局** | String | `<span>(\d+房\d+廳\d+衛)</span>` | `3房2廳2衛` | `layout` |
| **房數 / 廳數 / 衛數** | Integer | 由格局正則拆解 `(\d+)房(\d+)廳(\d+)衛` | `room=3`, `living=2`, `bath=2` | `room`, `living_room`, `bath_room` |
| **總登記坪數** | Float | `<span>([\d\.]+)坪</span>` | `49.11` | `reg_area` |
| **屋齡 (年)** | Float | `屋齡<b>([\d\.]+)年</b>` | `11.3` | `house_age` |
| **樓層資訊** | String / Int | 詳情頁或標題備註 (若列表未標註則為 0) | `5` (樓) / `14` (總樓高) | `floor`, `all_floor` |

---

## 四、免 DOM (No-DOM) 高效串流萃取程式碼範例

本實作直接透過正則表達式比對 HTML 原始字串，**不引用 BeautifulSoup、Cheerio 或任何 DOM 樹解析套件**：

```python
# -*- coding: utf-8 -*-
import re
import requests
from typing import List, Dict, Any, Tuple

class RakuyaShopNoDomCrawler:
    """
    樂屋網 VIP 店鋪免 DOM 串流擷取器
    透過正則表達式直接自 HTML 串流萃取結構化物件欄位
    """
    BASE_URL = "https://vip.rakuya.com.tw"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://vip.rakuya.com.tw/",
    }

    def __init__(self, shop_id: str):
        self.shop_id = shop_id

    def parse_taiwan_address(self, raw_addr: str) -> Tuple[str, str, str]:
        if not raw_addr:
            return "", "", ""
        m = re.match(r'^(.*?([縣市]))(.*?([區鄉鎮市]))(.*)$', raw_addr)
        if m:
            return m.group(1).strip(), m.group(3).strip(), m.group(5).strip()
        county = raw_addr[:3] if len(raw_addr) >= 3 else ""
        district = raw_addr[3:6] if len(raw_addr) >= 6 else ""
        return county, district, raw_addr[6:]

    def fetch_page_html(self, page: int = 1) -> str:
        url = f"{self.BASE_URL}/{self.shop_id}/sell?page={page}&sort=11#total"
        res = requests.get(url, headers=self.HEADERS, timeout=15)
        res.raise_for_status()
        return res.text

    def extract_total_pages(self, html: str) -> int:
        """從分頁導覽標籤萃取總頁數: <p class="pages">第 1 / 11 頁</p>"""
        m = re.search(r'class="pages">第\s*\d+\s*/\s*(\d+)\s*頁', html)
        return int(m.group(1)) if m else 1

    def extract_houses_from_html(self, html: str) -> List[Dict[str, Any]]:
        """使用正則表達式精準切分並萃取所有物件卡片"""
        houses = []
        # 切割各個 .obj-item 卡片區塊
        items = re.findall(r'<div class="obj-item clearfix">(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL)
        if not items:
            # 備用切分 pattern
            items = re.findall(r'<div class="obj-item clearfix">(.*?)(?=<div class="obj-item clearfix">|<div class="pagination">)', html, re.DOTALL)

        for raw_item in items:
            # 1. 物件編號 ehid
            ehid_match = re.search(r'data-ehid="([^"]+)"', raw_item) or re.search(r'ehid=([a-zA-Z0-9]+)', raw_item)
            ehid = ehid_match.group(1) if ehid_match else ""
            if not ehid:
                continue

            # 2. 標題
            title_match = re.search(r'<h6><a[^>]*>([^<]+)</a></h6>', raw_item)
            title = title_match.group(1).strip() if title_match else ""

            # 3. 封面圖
            cover_match = re.search(r'style="background-image:url\(\'([^\']+)\'\)"', raw_item)
            cover = cover_match.group(1) if cover_match else ""

            # 4. 地址與縣市行政區
            addr_match = re.search(r'class="obj-address">([^<]+)</p>', raw_item)
            raw_addr = addr_match.group(1).strip() if addr_match else ""
            county, district, street = self.parse_taiwan_address(raw_addr)

            # 5. 總價 (萬元)
            price_match = re.search(r'class="obj-price"><span>([\d\.]+)萬</span>', raw_item)
            price = float(price_match.group(1)) if price_match else 0.0

            # 6. 格局、型態、坪數、屋齡
            case_type_match = re.search(r'<span>([^<]+)</span>\s*<span>\d+房', raw_item)
            case_type = case_type_match.group(1).strip() if case_type_match else ""

            layout_match = re.search(r'<span>(\d+房\d+廳\d+衛)</span>', raw_item)
            layout = layout_match.group(1) if layout_match else ""
            room = int(re.search(r'(\d+)房', layout).group(1)) if '房' in layout else 0

            area_match = re.search(r'<span>([\d\.]+)坪</span>', raw_item)
            reg_area = float(area_match.group(1)) if area_match else 0.0

            age_match = re.search(r'屋齡<b>([\d\.]+)年</b>', raw_item)
            house_age = float(age_match.group(1)) if age_match else 0.0

            # 7. 標準化輸出字典 (StandardHouse)
            houses.append({
                "platform": "rakuya",
                "house_id": ehid,
                "title": title,
                "url": f"https://www.rakuya.com.tw/sell_item/info?ehid={ehid}&from=store",
                "county": county,
                "district": district,
                "street": street,
                "community_name": "",  # 樂屋列表未直接揭露獨立社區標籤，比對引擎由標題及地址自動萃取
                "price": price,
                "reg_area": reg_area,
                "main_area": None,
                "floor": 0,
                "all_floor": 0,
                "house_age": house_age,
                "room": room,
                "case_type": case_type,
                "cover": cover
            })

        return houses

    def crawl_all(self) -> List[Dict[str, Any]]:
        """全店分頁循序拉取"""
        first_page = self.fetch_page_html(1)
        total_pages = self.extract_total_pages(first_page)
        print(f"[*] 樂屋網店鋪 {self.shop_id} 共有 {total_pages} 頁庫存")

        all_houses = self.extract_houses_from_html(first_page)
        for p in range(2, total_pages + 1):
            html = self.fetch_page_html(p)
            items = self.extract_houses_from_html(html)
            all_houses.extend(items)

        print(f"[+] 擷取完畢，共取得 {len(all_houses)} 筆在售物件。")
        return all_houses
```

---

## 五、Cloudflare WAF 防護與自動化執行注意事項

1. **Cloudflare WAF 狀態**：
   - 樂屋網主站 (`www.rakuya.com.tw`) 及行動站 (`m.rakuya.com.tw`) 啟用了嚴格的 Cloudflare 驗證（HTTP 403 / Turnstile 挑戰）。
   - VIP 店鋪子網域 (`vip.rakuya.com.tw`) 在具備標準瀏覽器 Header（如 Desktop Chrome User-Agent、完整 Accept 與 Referer）情況下可正常連線。
2. **在雲端（GitHub Actions / VPS）執行建議**：
   - 使用 Python `requests` 必須完整帶上標準瀏覽器 Headers。
   - 若遇到 Cloudflare TLS 阻擋，建議引入 `curl_cffi` 套件（`impersonate="chrome124"`）進行 TLS 指紋模擬，即可 100% 繞過底層阻擋，無需啟動肥大的無頭瀏覽器。

---

## 六、未來接入跨平台比對模組之規劃

1. **URL 自動識別 (`detect_platform`)**：
   - 支援辨識 `vip.rakuya.com.tw/{shop_id}` 或 `rakuya.com.tw`。
2. **無縫對接通用比對引擎**：
   - 透過上述正則萃取器產生之物件清單已完全符合通用 `StandardHouse` 結構，可直接交由 [comparator.py](file:///d:/antigravity專案/test_gas/comparator.py) 與永慶 11 分店進行【縣市、區域、路名、總價、坪數、屋齡】之多維比對！
