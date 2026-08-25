import json
import requests
import pandas as pd
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

FECHA = "2026-07-28"
CANDIDATOS = {
    2066: "turbinacion Nacional",
    2079: "turbinacion (sin Nacional)",
    2065: "consumo Nacional",
    2078: "consumo (sin Nacional)",
}

for ind_id, nombre in CANDIDATOS.items():
    resp = requests.get(
        f"https://api.esios.ree.es/indicators/{ind_id}",
        headers=headers,
        params={
            "start_date": f"{FECHA}T00:00:00",
            "end_date": f"{FECHA}T01:00:00",
        },
        timeout=30,
    )
    data = resp.json().get("indicator", {})
    values = data.get("values", [])
    print(f"\n{ind_id} ({nombre})")
    print(f"  Geos: {data.get('geos')}")
    print(f"  Total valores: {len(values)}")
    for v in values[:3]:
        print(f"    {v.get('datetime')} → {v.get('value')} (geo_id={v.get('geo_id')}, geo_name={v.get('geo_name')})")