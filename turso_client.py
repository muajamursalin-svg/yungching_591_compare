import os
import requests
from typing import List, Dict, Any, Optional

class TursoClient:
    """
    Turso HTTP Pipeline Client
    透過 Turso 的 /v2/pipeline API 直接執行 SQL，無需任何 C/SQLite 底層編譯依賴。
    """
    def __init__(self, db_url: Optional[str] = None, auth_token: Optional[str] = None):
        if not db_url or not auth_token:
            # 自動從 .env 讀取
            env_file = os.path.join(os.path.dirname(__file__), ".env")
            if os.path.exists(env_file):
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            if k.strip() == "TURSO_DATABASE_URL" and not db_url:
                                db_url = v.strip()
                            elif k.strip() == "TURSO_AUTH_TOKEN" and not auth_token:
                                auth_token = v.strip()

        if not db_url or not auth_token:
            raise ValueError("缺少 TURSO_DATABASE_URL 或 TURSO_AUTH_TOKEN")

        self.base_url = db_url.replace("libsql://", "https://")
        self.auth_token = auth_token
        self.pipeline_url = f"{self.base_url}/v2/pipeline"
        self.headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        }

    def _format_arg(self, val: Any) -> Dict[str, Any]:
        """將 Python 變數格式化為 LibSQL 參數格式"""
        if val is None:
            return {"type": "null"}
        elif isinstance(val, bool):
            return {"type": "integer", "value": "1" if val else "0"}
        elif isinstance(val, int):
            return {"type": "integer", "value": str(val)}
        elif isinstance(val, float):
            return {"type": "float", "value": val}
        else:
            return {"type": "text", "value": str(val)}

    def execute(self, sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """執行單一 SQL 查詢或命令，並回傳格式化後的字典陣列"""
        stmt = {"sql": sql}
        if params:
            stmt["args"] = [self._format_arg(p) for p in params]

        payload = {
            "requests": [
                {"type": "execute", "stmt": stmt},
                {"type": "close"}
            ]
        }

        res = requests.post(self.pipeline_url, headers=self.headers, json=payload, timeout=20)
        res.raise_for_status()
        data = res.json()

        first_res = data.get("results", [])[0]
        if first_res.get("type") == "error":
            raise RuntimeError(f"Turso SQL Error: {first_res.get('error', {}).get('message')}")

        result_payload = first_res.get("response", {}).get("result", {})
        cols = [c.get("name") for c in result_payload.get("cols", [])]
        rows = result_payload.get("rows", [])

        parsed_rows = []
        for r in rows:
            row_dict = {}
            for col_name, cell in zip(cols, r):
                c_type = cell.get("type")
                c_val = cell.get("value")
                if c_type == "null":
                    row_dict[col_name] = None
                elif c_type == "integer":
                    row_dict[col_name] = int(c_val)
                elif c_type == "float":
                    row_dict[col_name] = float(c_val)
                else:
                    row_dict[col_name] = c_val
            parsed_rows.append(row_dict)

        return parsed_rows

    def batch(self, statements: List[tuple]) -> None:
        """批次執行多條 SQL 語句 (stmts: [(sql, params), ...])"""
        requests_list = []
        for item in statements:
            if isinstance(item, str):
                sql, params = item, None
            else:
                sql, params = item
            stmt = {"sql": sql}
            if params:
                stmt["args"] = [self._format_arg(p) for p in params]
            requests_list.append({"type": "execute", "stmt": stmt})

        requests_list.append({"type": "close"})
        payload = {"requests": requests_list}

        res = requests.post(self.pipeline_url, headers=self.headers, json=payload, timeout=60)
        res.raise_for_status()
        data = res.json()

        for idx, r in enumerate(data.get("results", [])):
            if r.get("type") == "error":
                raise RuntimeError(f"Turso Batch Error in stmt #{idx}: {r.get('error', {}).get('message')}")
