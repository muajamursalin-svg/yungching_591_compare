# 永慶房產集團 - 店鋪庫存底層 API 規格與串接指南

本文件記錄永慶房產集團（包含永慶不動產、永義房屋、有巢氏房屋、台慶不動產）經紀人店鋪庫存物件的底層 API 呼叫規格與實作細節。透過此 API，可直接取得純 JSON 格式的結構化房屋資料，**無需使用 Headless Browser 或解析 HTML DOM**。

---

## 一、 架構與雙層 API 設計

永慶店鋪的前端基於現代 Angular 框架，底層採用 RESTful JSON API 架構。庫存抓取由 **兩個關鍵 API** 組成：

1. **縣市區域選單 API (`/api/v1/information/case/area`)**：
   * 永慶店鋪的縣市區域下拉選單**只會動態回傳目前「實際擁有物件」的選項**，不會列出無庫存的區域。
2. **房屋庫存列表 API (`/api/v1/house/caselist`)**：
   * 支援兩種爬取模式：
     * **全店直抓模式**：不帶 `area` 參數，直接以最大每頁 100 筆分頁拉取全店鋪所有跨縣市庫存（最快）。
     * **區域篩選模式**：依下拉選單回傳的 `縣市-行政區` 依序分類抓取。

---

## 二、 API 規格詳解

### 1. 縣市行政區下拉選單 API (只回傳有庫存的選項)

* **請求方法**：`GET`
* **請求 URL**：
  ```http
  https://shop.yungching.com.tw/api/v1/information/case/area
  ```
* **請求參數**：
  | 參數名稱 | 必填 | 型態 | 說明 | 範例值 |
  | :--- | :---: | :---: | :--- | :--- |
  | `index` | **是** | String | 店鋪代碼（由網址 `shop.yungching.com.tw/{shopIndex}/list` 取得） | `0424522080` |

* **回傳範例**：
  ```json
  {
    "id": "ff59856c-dc42-4db3-8b50-73cd899c7fe3",
    "apiVersion": "1",
    "method": "/api/v1/information/case/area.GET",
    "status": "Success",
    "data": {
      "list": [
        {
          "county": "台中市",
          "districts": ["西屯區", "北屯區", "南屯區", "北區", "南區", "太平區", "大里區", "西區", "潭子區", "..."]
        },
        {
          "county": "南投縣",
          "districts": ["南投市", "埔里鎮", "草屯鎮", "魚池鄉"]
        }
      ]
    }
  }
  ```

---

### 2. 房屋庫存列表 API (Case List API)

* **請求方法**：`GET`
* **請求 URL**：
  ```http
  https://shop.yungching.com.tw/api/v1/house/caselist
  ```
* **驗證機制**：**無**（不需要 Cookie、不需要 Token）
* **請求標頭 (Headers)**：
  ```http
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36
  Referer: https://shop.yungching.com.tw/
  Accept: application/json, text/plain, */*
  ```

* **請求參數 (Query Parameters)**：
  | 參數名稱 | 必填 | 型態 | 說明 | 範例值 |
  | :--- | :---: | :---: | :--- | :--- |
  | `shop` | **是** | String | 店鋪代號 | `0424522080` |
  | `pg` | 否 | Int | 頁碼（Page，從 `1` 開始） | `1` |
  | `ps` | 否 | Int | 每頁筆數（Page Size，**最大支援 100**，預設 30） | `100` |
  | `area` | 否 | String | **區域篩選**：<br>• 不填：取得全店鋪跨區域所有庫存<br>• `縣市`（如 `台中市`）：取得該縣市庫存<br>• `縣市-行政區`（如 `台中市-西屯區`）：取得該區庫存 | `台中市-西屯區` |
  | `pinType` | 否 | Int | 坪數計算類型，固定帶 `0` | `0` |
  | `isAddRoom` | 否 | Bool | 是否包含加蓋房數，固定帶 `true` | `true` |
  | `od` | 否 | String | 排序方式 | 留空或指定代碼 |
  | `minPrice` | 否 | Float | 最低總價（萬元） | `1000` |
  | `maxPrice` | 否 | Float | 最高總價（萬元） | `3000` |
  | `type` | 否 | String | 型態篩選（如電梯大樓、別墅） | 留空或指定代碼 |

* **分頁中繼資料 (`pa`) 回傳結構**：
  ```json
  {
    "currentPage": 1,
    "totalPageCount": 3,
    "totalItemCount": 265,
    "rowStart": 1,
    "rowEnd": 100
  }
  ```

---

## 三、 欄位字典 (Field Dictionary)

每筆物件資料包含 **24 個核心屬性**，並具備高度結構化的子物件：

### 1. 房屋基本資訊
| 欄位名稱 | 型態 | 說明 | 範例值 |
| :--- | :---: | :--- | :--- |
| `caseSId` | Int | 物件流水號（**用於組合物件詳情頁網址**） | `7576035` |
| `caseKey` | String | 物件 UUID Key | `"7df7d08f-9840-4ba8-814b-76239f1197a2"` |
| `showCaseNo` | String | 永慶對外公開物件編號 | `"0058348"` |
| `caseName` | String | 物件完整標題名稱 | `"專任桃園中壢麗寶麗池次頂樓震後四房兩衛平車"` |
| `caseTypeName` | String | 建物型態名稱 | `"電梯大樓"` / `"別墅"` / `"透天厝"` / `"公寓"` |
| `purposeName` | String | 用途類別 | `"住宅"` / `"店面"` / `"土地"` / `"辦公"` |
| `buildAge` | Float | 屋齡（年，精確至小數點） | `26.3` |
| `caseFeature` | String | 物件特色描述全文（含換行符） | `"1.中壢千坪基地社區，大坪數唯一釋出..."` |
| `tag` | List[str] | 行銷標籤陣列 | `["新上"]` / `["降價"]` |

> **永慶房屋詳情頁 URL 規則**：
> `https://buy.yungching.com.tw/house/{caseSId}`  
> 例如：`https://buy.yungching.com.tw/house/7576035`

### 2. 價格資訊
| 欄位名稱 | 型態 | 說明 | 範例值 |
| :--- | :---: | :--- | :--- |
| `price` | Float | 目前總售價（萬元） | `1298.0` |
| `lastPrice` | Float | 降價前歷史售價（萬元） | `1328.0` |
| `downRatio` | Float | 降價比例（小數點表示） | `0.023`（表示降價 2.3%） |
| `isDiscount` | Bool | 是否為降價物件 | `true` |
| `carPrice` | String | 車位價格文字 | `""` / `"150萬"` |

### 3. 樓層資訊 (`floorInfo`)
API 直接提供數字型態的樓層數據：
| 欄位名稱 | 型態 | 說明 | 範例值 |
| :--- | :---: | :--- | :--- |
| `floorInfo.fromFloor` | Int | 所在起始樓層 | `11` |
| `floorInfo.toFloor` | Int | 所在終點樓層（單層與 fromFloor 相同；樓中樓為頂層） | `11` |
| `floorInfo.upFloor` | Int | 建物地上總樓層 | `12` |

> **樓層展示範例**：若 `fromFloor == toFloor`，則顯示 `11/12樓`；若不同則顯示 `11~12/12樓`。

### 4. 坪數面積資訊 (`pinInfo`)
| 欄位名稱 | 型態 | 說明 | 範例值 |
| :--- | :---: | :--- | :--- |
| `pinInfo.regArea` | Float | 建物登記總坪數 | `54.26` |
| `pinInfo.mainArea` | Float | 主建物（室內實坪）面積 | `31.08` |
| `pinInfo.landArea` | Float | 土地持分坪數 | `5.21` |
| `pinInfo.platformArea` | Float | 陽台 / 平台坪數 | `0.0` |
| `pinInfo.porchArea` | Float | 雨遮 / 玄關坪數 | `3.52` |
| `pinInfo.gazeboArea` | Float | 露台 / 涼亭坪數 | `0.0` |
| `pinInfo.mezzanineArea` | Float | 夾層合法面積 | `0.0` |

### 5. 格局與車位 (`patternInfo`)
| 欄位名稱 | 型態 | 說明 | 範例值 |
| :--- | :---: | :--- | :--- |
| `patternInfo.room` | Float | 房間數量 | `4.0` |
| `patternInfo.livingRoom` | Float | 客餐廳數量 | `2.0` |
| `patternInfo.bathRoom` | Float | 衛浴數量 | `2.0` |
| `parking` | String | 車位型態說明 | `"坡道平面車位"` / `"無"` |

### 6. 地理位置與社區
| 欄位名稱 | 型態 | 說明 | 範例值 |
| :--- | :---: | :--- | :--- |
| `address` | String | 物件地址（縣市區路段） | `"桃園市中壢區後寮二路"` |
| `communityInfo.communityId` | Int | 所屬社區編號 | `1674` |
| `communityInfo.communityName`| String | 所屬社區名稱 | `"麗寶麗池"` |

### 7. 屬性標籤與媒體 (`labelsInfo`)
| 欄位名稱 | 型態 | 說明 | 範例值 |
| :--- | :---: | :--- | :--- |
| `cover` | String | 封面圖片 CDN URL | `"//yccdn.yungching.com.tw/v1/image/?key=..."` |
| `labelsInfo.isExclusive` | Bool | 是否為**專任委託** | `true` |
| `labelsInfo.isGold` | Bool | 是否為**精選/黃金物件** | `true` |
| `labelsInfo.isVr` | Bool | 是否支援 VR 線上看屋 | `false` |
| `labelsInfo.isIntroVideo` | Bool | 是否有介紹影片 | `false` |

---

## 四、 完整 Python 實作程式碼

以下程式提供模組化封裝，支援：
1. 自動取得該店鋪**擁有庫存的縣市下拉選單**。
2. **全店高速爬取**（單次請求 100 筆，265 筆只需 3 次請求）。
3. **依下拉選單分區爬取**（指定 `area='台中市-西屯區'`）。

```python
import time
import requests
from typing import List, Dict, Any, Optional

class YungchingShopClient:
    BASE_DOMAIN = "https://shop.yungching.com.tw"
    
    def __init__(self, delay_sec: float = 0.5):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": "https://shop.yungching.com.tw/",
            "Accept": "application/json, text/plain, */*",
        })
        self.delay_sec = delay_sec

    def get_available_areas(self, shop_index: str) -> List[Dict[str, Any]]:
        """
        取得店鋪有物件的縣市與行政區下拉選單
        :param shop_index: 店鋪代碼，如 '0424522080'
        :return: 包含縣市與行政區的清單
        """
        url = f"{self.BASE_DOMAIN}/api/v1/information/case/area"
        params = {"index": shop_index}
        res = self.session.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        return data.get("data", {}).get("list", [])

    def fetch_inventory(self, shop_index: str, area: Optional[str] = None, page_size: int = 100) -> List[Dict[str, Any]]:
        """
        分頁抓取指定店鋪的庫存物件
        :param shop_index: 店鋪代碼
        :param area: 篩選區域（例如 '台中市-西屯區' 或 '台中市'，傳 None 則爬取全店鋪所有跨區物件）
        :param page_size: 每頁數量（API 最大支援 100）
        :return: 房屋物件清單
        """
        url = f"{self.BASE_DOMAIN}/api/v1/house/caselist"
        all_houses = []
        page = 1

        while True:
            params = {
                "shop": shop_index,
                "pg": page,
                "ps": page_size,
                "pinType": 0,
                "isAddRoom": "true"
            }
            if area:
                params["area"] = area

            try:
                res = self.session.get(url, params=params, timeout=10)
                res.raise_for_status()
                json_data = res.json()
            except Exception as e:
                print(f"[Error] 抓取失敗 (頁碼 {page}): {e}")
                break

            data = json_data.get("data", {})
            items = data.get("list", [])
            pa = data.get("pa", {})
            total_pages = pa.get("totalPageCount", 1)
            total_items = pa.get("totalItemCount", 0)

            if not items:
                break

            all_houses.extend(items)
            area_label = area if area else "全店跨區"
            print(f"[{area_label}] 第 {page}/{total_pages} 頁，累積已抓取: {len(all_houses)} / {total_items} 筆")

            if page >= total_pages:
                break

            page += 1
            if self.delay_sec > 0:
                time.sleep(self.delay_sec)

        return all_houses


if __name__ == "__main__":
    client = YungchingShopClient(delay_sec=0.3)
    shop_id = "0424522080"

    # 1. 取得該店鋪有庫存的縣市下拉選單
    print("=== 步驟 1: 讀取下拉選單有物件的選項 ===")
    areas = client.get_available_areas(shop_id)
    for a in areas:
        print(f"縣市: {a['county']} | 包含行政區: {', '.join(a['districts'])}")

    # 2. 爬取全店所有庫存 (不傳 area，直接快速抓取全店 265 筆)
    print("\n=== 步驟 2: 爬取全店庫存 ===")
    all_inventory = client.fetch_inventory(shop_id, area=None, page_size=100)
    print(f"\n全店爬取完成！共取得 {len(all_inventory)} 筆資料。")

    # 範例列印前 3 筆
    for i, h in enumerate(all_inventory[:3], 1):
        fl = h.get("floorInfo", {})
        pin = h.get("pinInfo", {})
        print(f"{i}. [{h.get('caseTypeName')}] {h.get('caseName')}")
        print(f"   總價: {h.get('price')}萬 | 坪數: {pin.get('regArea')}坪 | 樓層: {fl.get('fromFloor')}/{fl.get('upFloor')}樓")
        print(f"   詳情頁: https://buy.yungching.com.tw/house/{h.get('caseSId')}")
```

---

## 五、 Node.js (JavaScript) 實作範例

```javascript
async function fetchYungchingShop(shopIndex, area = null) {
  const baseUrl = "https://shop.yungching.com.tw/api/v1/house/caselist";
  let page = 1;
  const pageSize = 100;
  const allHouses = [];

  while (true) {
    const params = new URLSearchParams({
      shop: shopIndex,
      pg: page.toString(),
      ps: pageSize.toString(),
      pinType: "0",
      isAddRoom: "true",
    });
    if (area) {
      params.append("area", area);
    }

    const res = await fetch(`${baseUrl}?${params.toString()}`, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://shop.yungching.com.tw/",
        "Accept": "application/json",
      },
    });

    if (!res.ok) throw new Error(`HTTP Error: ${res.status}`);

    const result = await res.json();
    const items = result.data?.list || [];
    const pa = result.data?.pa || {};

    if (items.length === 0) break;
    allHouses.push(...items);

    if (page >= (pa.totalPageCount || 1)) break;
    page++;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }

  return allHouses;
}

// 執行
fetchYungchingShop("0424522080").then((items) => {
  console.log(`成功抓取永慶店鋪物件，共 ${items.length} 筆！`);
});
```

---

## 六、 實務開發要點與注意事項

1. **每頁筆數最佳化**：
   * 永慶 API 預設為 `ps=30`，但**實測支援最大 `ps=100`**。對於數百筆庫存的店鋪，建議一律設為 `ps=100`，只需少數幾次 HTTP 請求即可下載全部庫存，降低被頻率限制的風險。
2. **`area` 參數格式**：
   * 篩選單一行政區時，格式**必須包含縣市與連字號**（例如 `台中市-西屯區`），如果只傳 `西屯區` 會回傳 0 筆。
   * 若傳 `台中市`，則會取得該縣市下的所有行政區物件。
3. **樓層資訊提取**：
   * 樓層資訊以結構化物件 `floorInfo` 存在，不需要自行正規表達式解析文字：
     * `fromFloor`：起算所在樓層。
     * `toFloor`：結束所在樓層。
     * `upFloor`：地上總樓層。
4. **降價物件追蹤**：
   * 永慶 API 原生提供 `isDiscount`（是否降價）、`lastPrice`（降價前原價）與 `downRatio`（降幅比率），可用於快速篩選特價與屋主讓利物件。
