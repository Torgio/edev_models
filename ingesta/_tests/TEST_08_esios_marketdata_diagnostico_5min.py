"""
DIAGNOSTICO 6 — Descarga horaria del dia completo para varios indicadores
reales, usando time_agg=average correctamente. Genera una tabla ancha
para comparar visualmente contra el balance oficial (SIN program=P48).
"""

import json
import time
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

FECHA = "2026-07-26"
PENINSULA_GEO_ID = 8741

INDICADORES = {
    1293: "demanda_real_mw",
    551:  "gen_wind_mw",
    546:  "gen_hidro_real_mw",
    549:  "gen_nuclear_real_mw",
    550:  "gen_ciclocomb_real_mw",
    547:  "gen_coal_real_mw",
    553:  "gen_cogen_real_mw",
    1295: "gen_solar_mw",
}


def descargar_dia_completo(ind_id, nombre):
    resp = requests.get(
        f"https://api.esios.ree.es/indicators/{ind_id}",
        headers=headers,
        params={
            "start_date": f"{FECHA}T00:00:00+02:00",
            "end_date":   f"{FECHA}T23:59:59+02:00",
            "time_trunc": "hour",
            "time_agg":   "average",
            "geo_ids[]":  PENINSULA_GEO_ID,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  {nombre}: ERROR HTTP {resp.status_code}")
        return None

    values = resp.json().get("indicator", {}).get("values", [])
    if not values:
        print(f"  {nombre}: sin datos")
        return None

    df = pd.DataFrame(values)[["datetime", "value"]]
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["hora"] = df["datetime"].dt.strftime("%H:%M")
    df = df.rename(columns={"value": nombre})
    return df[["hora", nombre]]


tabla = None
for ind_id, nombre in INDICADORES.items():
    print(f"Descargando {nombre} (indicador {ind_id})...")
    df = descargar_dia_completo(ind_id, nombre)
    if df is not None:
        tabla = df if tabla is None else tabla.merge(df, on="hora", how="outer")
    time.sleep(0.3)

if tabla is not None:
    tabla = tabla.sort_values("hora").reset_index(drop=True)
    print(f"\n\n=== TABLA COMPLETA — {FECHA} (time_agg=average) ===")
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print(tabla.to_string(index=False))

    tabla.to_csv(f"comparativa_{FECHA}.csv", index=False)
    print(f"\nGuardado en comparativa_{FECHA}.csv")