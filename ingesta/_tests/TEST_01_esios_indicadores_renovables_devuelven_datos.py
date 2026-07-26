import json
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"
with open(CREDENTIALS_PATH) as f:
    creds = json.load(f)

headers = {
    "Host":         creds["Host"],
    "x-api-key":    creds["x-api-key"],
    "Accept":       "application/json; application/vnd.esios-api-v2+json",
    "Content-Type": "application/json",
}

IDS_A_PROBAR = [1482, 1483, 1484, 1485, 1486, 1487, 1488, 1489, 1490, 1491,
                10300, 10301, 10302, 10303, 10304]

hoy = datetime.now()
start_date = (hoy - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
end_date   = (hoy + timedelta(days=2)).strftime("%Y-%m-%dT23:00:00")

resultados = []
for ind_id in IDS_A_PROBAR:
    try:
        # SIN geo_agg / geo_trunc esta vez
        resp = requests.get(
            f"https://api.esios.ree.es/indicators/{ind_id}",
            headers=headers,
            params={"start_date": start_date, "end_date": end_date},
            timeout=30
        )
        data = resp.json()
        values = data.get("indicator", {}).get("values", [])
        nombre_real = data.get("indicator", {}).get("name", "??")

        if not values:
            resultados.append({"id": ind_id, "nombre_api": nombre_real, "filas": 0, "geo_names": None, "min": None, "max": None})
            continue

        df = pd.json_normalize(values)
        geo_names = df["geo_name"].unique().tolist() if "geo_name" in df.columns else []
        resultados.append({
            "id": ind_id, "nombre_api": nombre_real, "filas": len(df),
            "geo_names": geo_names, "min": df["value"].min(), "max": df["value"].max()
        })
    except Exception as e:
        resultados.append({"id": ind_id, "nombre_api": "ERROR", "filas": 0, "geo_names": None, "min": None, "max": str(e)[:100]})
    time.sleep(0.5)

df_resumen = pd.DataFrame(resultados)
pd.set_option("display.max_colwidth", 60)
pd.set_option("display.width", 200)
print(df_resumen.to_string(index=False))