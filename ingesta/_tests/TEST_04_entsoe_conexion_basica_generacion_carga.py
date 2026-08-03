import pandas as pd
from entsoe import EntsoePandasClient
import json
from pathlib import Path

creds = json.load(open(Path(__file__).parent / "credentials.json"))
client = EntsoePandasClient(api_key=creds["entsoe_token"])

start = pd.Timestamp("2026-06-01", tz="Europe/Madrid")
end   = pd.Timestamp("2026-06-03", tz="Europe/Madrid")

print("=== Generacion real por tipo ===")
df = client.query_generation("ES", start=start, end=end)
print(df.head(5))
print(f"Columnas: {df.columns.tolist()}")

print("\n=== Carga real ===")
df2 = client.query_load("ES", start=start, end=end)
print(df2.head(5))