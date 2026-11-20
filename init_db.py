import os
from turso_client import TursoClient

def init_database():
    print("Connecting to Turso...")
    client = TursoClient()
    
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    # Split by semicolon
    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
    print(f"Executing {len(statements)} DDL statements...")

    for i, stmt in enumerate(statements, 1):
        client.execute(stmt)
        print(f"  [{i}/{len(statements)}] Executed: {stmt.split()[0]} {stmt.split()[1] if len(stmt.split()) > 1 else ''}...")

    # Check tables
    tables = client.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    table_names = [t["name"] for t in tables]
    print("\n✅ 資料表建立成功！目前 Turso 中的資料表：")
    for name in table_names:
        print(f"  - {name}")

if __name__ == "__main__":
    init_database()
