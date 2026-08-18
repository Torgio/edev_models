"""
TFM Energia UCM — Verificacion de indicadores ESIOS que se revisan tras publicarse
Compara el valor guardado en esios_forecast_da contra lo que la API de ESIOS
devuelve AHORA MISMO, para el mismo dia y hora. Si difieren, es prueba directa
de que el indicador se corrigio despues de la primera carga.

Pensado para ensenar en vivo: la diferencia se ve en pantalla, no hay que
confiar en un informe estatico.

Uso:
    python check_tables/verificar_revision_indicadores.py
    python check_tables/verificar_revision_indicadores.py --indicador demanda_residual_prev_mw --dias 2,5,10,15
"""

import argparse
import sys
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import psycopg2

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

TZ = ZoneInfo("Europe/Madrid")

# columna en esios_forecast_da -> indicador ESIOS (id) usado por el pipeline
INDICADORES = {
    "demanda_residual_prev_mw": 10249,
    "potencia_indisp_pbf_mw":   462,
    "demanda_prev_mw":          1775,
    "gen_wind_prev_mw":         1777,
    "gen_solar_pv_prev_mw":     1779,
}


def fetch_live(headers, ind_id: int, target: date) -> dict:
    """Descarga el indicador de la API AHORA MISMO para el dia target."""
    start_utc = target - timedelta(days=1)
    end_utc = target + timedelta(days=1)
    url = (f"https://api.esios.ree.es/indicators/{ind_id}"
           f"?start_date={start_utc}T00:00:00&end_date={end_utc}T23:59:59"
           f"&time_trunc=hour&time_agg=average")
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    valores = r.json().get("indicator", {}).get("values", [])
    out = {}
    for v in valores:
        dt = datetime.fromisoformat(v["datetime_utc"].replace("Z", "+00:00"))
        if dt.astimezone(TZ).date() == target:
            out[dt] = float(v["value"])
    return out


def comparar(conn, columna: str, ind_id: int, target: date):
    live = {}
    headers, _ = load_config()
    try:
        live = fetch_live(headers, ind_id, target)
    except Exception as e:
        print(f"  ERROR consultando API: {e}")
        return

    ini = datetime(target.year, target.month, target.day, 0, 0, tzinfo=TZ).astimezone(timezone.utc)
    fin = datetime(target.year, target.month, target.day, 23, 59, tzinfo=TZ).astimezone(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT datetime, {columna} FROM esios_forecast_da WHERE datetime >= %s AND datetime < %s ORDER BY datetime",
            (ini, fin),
        )
        stored = {row[0]: (float(row[1]) if row[1] is not None else None) for row in cur.fetchall()}

    comparadas = diffs = 0
    ejemplos = []
    for dt, live_val in live.items():
        dt_utc = dt.astimezone(timezone.utc)
        if dt_utc in stored:
            comparadas += 1
            db_val = stored[dt_utc]
            if db_val is None or abs(db_val - live_val) > 0.5:
                diffs += 1
                if len(ejemplos) < 3:
                    ejemplos.append((dt.astimezone(TZ).strftime("%H:%M"), db_val, live_val))

    print(f"  {target}: {comparadas} horas comparadas, {diffs} con diferencia")
    for hora, db_val, live_val in ejemplos:
        print(f"      {hora}  BD={db_val}  API_ahora={live_val}")


def main():
    parser = argparse.ArgumentParser(description="Verifica si un indicador ESIOS se revisa tras publicarse")
    parser.add_argument("--indicador", default="demanda_residual_prev_mw",
                        choices=list(INDICADORES.keys()),
                        help="Columna de esios_forecast_da a verificar")
    parser.add_argument("--dias", default="2,5,10,15",
                        help="Dias hacia atras a comprobar, separados por coma")
    args = parser.parse_args()

    columna = args.indicador
    ind_id = INDICADORES[columna]
    dias = [int(d) for d in args.dias.split(",")]

    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)

    print(f"Verificando: {columna} (indicador ESIOS {ind_id})")
    print(f"Comparando valor guardado en BD vs. API en vivo, ahora mismo\n")

    for d in dias:
        comparar(conn, columna, ind_id, date.today() - timedelta(days=d))

    conn.close()


if __name__ == "__main__":
    main()
