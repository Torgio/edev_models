import json
import requests
from pathlib import Path

CREDENTIALS_PATH = Path(__file__).parent.parent / "credentials.json"
with open(CREDENTIALS_PATH) as f:
    creds = json.load(f)

headers = {
    "Host":         creds["Host"],
    "x-api-key":    creds["x-api-key"],
    "Accept":       "application/json; application/vnd.esios-api-v2+json",
    "Content-Type": "application/json",
}

# Pedimos el DIA COMPLETO sin time_trunc, para ver la granularidad nativa real
resp = requests.get(
    "https://api.esios.ree.es/indicators/561",
    headers=headers,
    params={
        "start_date": "2026-07-28T00:00:00",
        "end_date": "2026-07-28T23:59:59",
        "geo_ids[]": 8741,
    },
    timeout=30,
)
data = resp.json().get("indicator", {}).get("values", [])
print(f"Total valores devueltos para el DIA COMPLETO: {len(data)}")
for v in data[:8]:
    print(f"  {v['datetime']} → {v['value']}")

print(f"\nSuma de las primeras 4 muestras (deberia ser la hora 00:00): "
      f"{sum(v['value'] for v in data[:4]):.2f}")