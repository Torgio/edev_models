"""
TFM Energia UCM — Verificacion + grafica de potencia disponible
Edita las variables START_YEAR / START_MONTH / END_YEAR / END_MONTH abajo
y ejecuta el script directamente (sin argumentos).

Solo lectura: NO escribe en la base de datos. Descarga los 8 indicadores
de potencia disponible (generacion convencional), promedio diario, y genera
CSV + grafica. Reutilizable como base para el futuro script de carga historica.
"""

import json
import time
import calendar
import requests
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import date

# ══════════════════════════════════════════════════════════════════
# EDITA ESTO PARA CAMBIAR EL RANGO A CONSULTAR
# ══════════════════════════════════════════════════════════════════
START_YEAR = 2026
START_MONTH = 6

END_YEAR = 2026
END_MONTH = 6
# ══════════════════════════════════════════════════════════════════

CREDENTIALS_PATH = Path(__file__).parent.parent / "credentials.json"
with open(CREDENTIALS_PATH) as f:
    creds = json.load(f)

headers = {
    "Host":         creds["Host"],
    "x-api-key":    creds["x-api-key"],
    "Accept":       "application/json; application/vnd.esios-api-v2+json",
    "Content-Type": "application/json",
}

PENINSULA_GEO_ID = 8741

INDICATORS_AVAILABLE = {
    472: "hydro_mw",
    473: "pump_mw",
    474: "nuclear_mw",
    475: "coal_antracita_mw",
    476: "coal_subbituminosa_mw",
    477: "ccgt_mw",
    478: "fuel_mw",
    479: "gas_turbine_mw",
}


def fetch_indicator_daily_avg(indicator_id, start: date, end: date):
    """
    Descarga un indicador de disponible para un rango [start, end], agregado
    a Peninsula, con promedio diario (time_trunc=day, time_agg=avg).
    Retorna un DataFrame con columnas [fecha, value], o None si falla/vacio.

    Esta funcion es la pieza reutilizable para el futuro script de carga
    historica a esios_capacity_available.
    """
    start_str = start.strftime("%Y-%m-%dT00:00:00")
    end_str = end.strftime("%Y-%m-%dT23:00:00")

    try:
        resp = requests.get(
            f"https://api.esios.ree.es/indicators/{indicator_id}",
            headers=headers,
            params={
                "start_date": start_str,
                "end_date": end_str,
                "time_trunc": "day",
                "time_agg": "avg",
                "geo_agg": "sum",
                "geo_trunc": "electric_system",
            },
            timeout=60
        )
        if resp.status_code != 200:
            print(f"  Indicador {indicator_id}: ERROR HTTP {resp.status_code} — {resp.text[:150]}")
            return None

        values = resp.json().get("indicator", {}).get("values", [])
        if not values:
            print(f"  Indicador {indicator_id}: sin datos")
            return None

        df = pd.json_normalize(values)
        peninsula = df[df["geo_id"] == PENINSULA_GEO_ID].copy()

        if peninsula.empty:
            print(f"  Indicador {indicator_id}: sin fila Peninsula")
            return None

        peninsula["fecha"] = pd.to_datetime(peninsula["datetime"]).dt.date
        return peninsula[["fecha", "value"]]

    except Exception as e:
        print(f"  Indicador {indicator_id}: ERROR — {str(e)[:100]}")
        return None


def descargar_rango(start: date, end: date) -> pd.DataFrame:
    """
    Descarga los 8 indicadores de disponible para el rango [start, end]
    y devuelve una tabla ancha: filas=fecha, columnas=tecnologia (coal_mw ya sumado).
    """
    series = {}
    for ind_id, col in INDICATORS_AVAILABLE.items():
        print(f"Descargando {col} (indicador {ind_id})...")
        df_serie = fetch_indicator_daily_avg(ind_id, start, end)
        if df_serie is not None:
            series[col] = df_serie.set_index("fecha")["value"]
        time.sleep(0.3)

    if not series:
        return pd.DataFrame()

    df_final = pd.DataFrame(series)

    coal_cols = [c for c in ["coal_antracita_mw", "coal_subbituminosa_mw"] if c in df_final.columns]
    if coal_cols:
        df_final["coal_mw"] = df_final[coal_cols].sum(axis=1, skipna=True)
        df_final = df_final.drop(columns=coal_cols)

    df_final.index.name = "fecha"
    return df_final.sort_index()


def main():
    start = date(START_YEAR, START_MONTH, 1)
    ultimo_dia = calendar.monthrange(END_YEAR, END_MONTH)[1]
    end = date(END_YEAR, END_MONTH, ultimo_dia)
    es_un_mes = (start.year == end.year and start.month == end.month)

    print(f"Verificando potencia DISPONIBLE desde {start} hasta {end}...\n")

    df_final = descargar_rango(start, end)

    if df_final.empty:
        print("\nNo se obtuvo ningun dato. Abortando.")
        return

    print(f"\n--- Tabla diaria ({len(df_final)} dias) ---")
    print(df_final.to_string() if es_un_mes else df_final.describe().to_string())

    tag = start.strftime("%Y_%m") if es_un_mes else f"{start.strftime('%Y_%m')}_a_{end.strftime('%Y_%m')}"

    filename_csv = f"disponible_{tag}.csv"
    df_final.to_csv(filename_csv)
    print(f"\nGuardado en {filename_csv}")

    # ── Grafica ──
    fig, ax = plt.subplots(figsize=(12, 6))

    if es_un_mes:
        for col in df_final.columns:
            ax.plot(df_final.index, df_final[col], marker="o", markersize=3, label=col)
        titulo = f"Potencia disponible por tecnologia — {start.strftime('%B %Y')}"
    else:
        df_mensual = df_final.copy()
        df_mensual.index = pd.to_datetime(df_mensual.index)
        df_mensual = df_mensual.resample("MS").mean()
        for col in df_mensual.columns:
            ax.plot(df_mensual.index, df_mensual[col], marker="o", markersize=3, label=col)
        titulo = f"Potencia disponible por tecnologia (promedio mensual) — {start.strftime('%Y-%m')} a {end.strftime('%Y-%m')}"

    ax.set_title(titulo)
    ax.set_xlabel("Fecha")
    ax.set_ylabel("MW")
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1), fontsize=8)
    ax.grid(True, alpha=0.3)
    plt.xticks(rotation=45)
    plt.tight_layout()

    filename_png = f"disponible_{tag}.png"
    plt.savefig(filename_png, dpi=150)
    print(f"Grafica guardada en {filename_png}")
    plt.show()


if __name__ == "__main__":
    main()