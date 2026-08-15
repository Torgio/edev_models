"""
esios_forecast_da_recalculo.py - correccion del bug x4
=======================================================

Diagnostico (TEST_416, 15/08/2026)
----------------------------------
El pipeline pide time_trunc=hour SIN time_agg. El default de ESIOS es SUM, asi
que los indicadores nativos cuartohorarios entran multiplicados por 4 exacto.

  INFLADAS x4 (ratio 4.000 verificado)   nativos=96
    10249 demanda_residual_prev_mw        83865.00 -> 20966.25
    1844  ntc_fr_imp_prev_mw               8000.00 ->  2000.00
    1845  ntc_fr_exp_prev_mw              13188.00 ->  3297.00
    1846  ntc_pt_imp_prev_mw               2400.00 ->   600.00
    1848  ntc_pt_exp_prev_mw              14060.00 ->  3515.00
    1849  ntc_ma_imp_prev_mw              17820.00 ->  4455.00
    1850  ntc_ma_exp_prev_mw               3600.00 ->   900.00
    2563  demanda_mercado_prev_mw         99847.00 -> 24961.75
    543   gen_solartermica_prev_mw         1861.00 ->   465.25

  LIMPIAS (ratio 1.000)                  nativos=24
    1775 demanda_prev_mw, 1777 gen_wind_prev_mw, 1779 gen_solar_pv_prev_mw,
    10358 gen_renovables_prev_mw, 462 potencia_indisp_pbf_mw

Por que NO se reescribe a ciegas
--------------------------------
Que un indicador sea cuartohorario HOY no prueba que lo fuera en 2020. ESIOS ya
cambio la granularidad del 600 en enero de 2025. Si 2563 o 543 fueron horarios
en el pasado, el x4 solo afecta al tramo reciente y sobrescribir todo estropea
el bueno. Por eso se compara valor a valor y solo se actualiza si la diferencia
supera la tolerancia.

Las cinco columnas limpias se descargan como CONTROL: se comparan pero NUNCA se
escriben. Si aparecen diferencias ahi, hay un problema distinto al x4 y el
script avisa. Es la misma verificacion que se uso en las recalculaciones de
esios_marketdata.

NO ESCRIBE salvo que se pase --ejecutar.

Uso
---
    python esios_forecast_da_recalculo.py --desde 2026-06-01 --hasta 2026-06-30
    python esios_forecast_da_recalculo.py --desde 2026-06-01 --hasta 2026-06-30 --ejecutar
    python esios_forecast_da_recalculo.py --desde 2020-01-01 --hasta 2026-08-14 --ejecutar
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd
import psycopg2
import requests
from dateutil.relativedelta import relativedelta
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent))

API = "https://api.esios.ree.es/indicators"
TABLA = "esios_forecast_da"
GEO_PENINSULA = 8741
TZ = "Europe/Madrid"
TOLERANCIA = Decimal("0.01")
PAUSA = 0.35

CREDENTIALS_CANDIDATAS = [
    Path(__file__).parent / "credentials.json",
    Path(__file__).parent.parent / "credentials.json",
    Path.home() / "scripts" / "ingesta" / "credentials.json",
]

# MAPEO VERIFICADO contra ingesta/esios_forecast_da_pipeline.py (15/08/2026).
# El orden de las NTC es por SENTIDO, no por frontera:
#   1844-1846 = importacion (FR, PT, MA)
#   1848-1850 = exportacion (FR, PT, MA)
# Asumir "una frontera cada dos ids" desplaza las columnas y escribe la
# capacidad francesa en la portuguesa. No cambiar sin releer el pipeline.
AFECTADAS = {
    10249: "demanda_residual_prev_mw",
    1844:  "ntc_fr_imp_prev_mw",
    1848:  "ntc_fr_exp_prev_mw",
    1845:  "ntc_pt_imp_prev_mw",
    1849:  "ntc_pt_exp_prev_mw",
    1846:  "ntc_ma_imp_prev_mw",
    1850:  "ntc_ma_exp_prev_mw",
    2563:  "demanda_mercado_prev_mw",
    543:   "gen_solartermica_prev_mw",
}

CONTROL = {
    1775:  "demanda_prev_mw",
    1777:  "gen_wind_prev_mw",
    1779:  "gen_solar_pv_prev_mw",
    10358: "gen_renovables_prev_mw",
    462:   "potencia_indisp_pbf_mw",
}

# cap_baleares_prev_mw (570) queda FUERA: no se publica con geo_id=8741 y su
# geo correcto esta sin determinar. Pendiente de verificar aparte.


def redondear(v) -> Decimal | None:
    """ROUND_HALF_UP, nunca el half-to-even de round()."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def credenciales() -> dict:
    for r in CREDENTIALS_CANDIDATAS:
        if r.exists():
            return json.loads(r.read_text(encoding="utf-8"))
    raise SystemExit("No se encontro credentials.json")


def token_esios(cred) -> str:
    for c in ("x-api-key", "esios_token", "token_esios", "api_key"):
        if c in cred:
            return cred[c]
    raise SystemExit("No hay token de ESIOS en credentials.json")


def conectar(cred):
    return psycopg2.connect(host=cred["db_host"], port=cred.get("db_port", 5432),
                            dbname=cred["db_name"], user=cred["db_user"],
                            password=cred["db_password"])


def col_tiempo(conn) -> str:
    """Detecta si la columna se llama datetime o time. No hardcodear:
    el rename estaba pendiente y puede haberse aplicado ya."""
    with conn.cursor() as cur:
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_name = %s
                         AND data_type LIKE 'timestamp%%'
                       ORDER BY ordinal_position LIMIT 1""", (TABLA,))
        r = cur.fetchone()
    if not r:
        raise SystemExit(f"No se encontro columna temporal en {TABLA}")
    return r[0]


def descargar(ind: int, desde: date, hasta: date, token: str) -> pd.Series:
    """Serie horaria correcta: time_agg=average."""
    time.sleep(PAUSA)
    r = requests.get(
        f"{API}/{ind}",
        params={"start_date": f"{desde}T00:00", "end_date": f"{hasta}T23:59",
                "time_trunc": "hour", "time_agg": "average",
                "geo_ids[]": [GEO_PENINSULA]},
        headers={"Accept": "application/json; application/vnd.esios-api-v1+json",
                 "x-api-key": token},
        timeout=90)
    r.raise_for_status()
    vals = r.json()["indicator"]["values"]
    if not vals:
        return pd.Series(dtype=float)
    df = pd.DataFrame(vals)
    idx = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert(TZ)
    return pd.Series(df["value"].values, index=idx).sort_index()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--desde", type=date.fromisoformat, required=True)
    p.add_argument("--hasta", type=date.fromisoformat, required=True)
    p.add_argument("--ejecutar", action="store_true",
                   help="Sin este flag solo informa, no escribe")
    args = p.parse_args()

    cred = credenciales()
    token = token_esios(cred)
    conn = conectar(cred)
    tcol = col_tiempo(conn)

    print("=" * 74)
    print(f"Recalculo {TABLA}   {args.desde} .. {args.hasta}")
    print(f"Columna temporal detectada: '{tcol}'")
    print("MODO: " + ("ESCRITURA" if args.ejecutar else "SIMULACION (--ejecutar para escribir)"))
    print("=" * 74)

    corregidas = {c: 0 for c in AFECTADAS.values()}
    alertas = {c: 0 for c in CONTROL.values()}
    sin_fila = 0

    mes = args.desde.replace(day=1)
    while mes <= args.hasta:
        fin_mes = min(mes + relativedelta(months=1) - relativedelta(days=1),
                      args.hasta)
        ini_mes = max(mes, args.desde)

        series = {}
        for ind, col in {**AFECTADAS, **CONTROL}.items():
            try:
                s = descargar(ind, ini_mes, fin_mes, token)
                if len(s):
                    series[col] = s
            except Exception as e:
                print(f"  {ini_mes:%Y-%m} {col}: ERROR {str(e)[:60]}")

        if not series:
            print(f"  {ini_mes:%Y-%m}  sin datos")
            mes += relativedelta(months=1)
            continue

        api = pd.DataFrame(series)

        cols_bd = list(AFECTADAS.values()) + list(CONTROL.values())
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT "{tcol}", {", ".join(cols_bd)} FROM {TABLA} '
                f'WHERE "{tcol}" >= %s AND "{tcol}" < %s',
                (ini_mes, fin_mes + relativedelta(days=1)))
            filas = cur.fetchall()
        bd = pd.DataFrame(filas, columns=[tcol] + cols_bd).set_index(tcol)
        bd.index = pd.to_datetime(bd.index, utc=True).tz_convert(TZ)

        actualizaciones = []
        for ts in api.index:
            if ts not in bd.index:
                sin_fila += 1
                continue

            nuevos, cambia = {}, False
            for col in AFECTADAS.values():
                if col not in api.columns:
                    continue
                nv, av = redondear(api.at[ts, col]), redondear(bd.at[ts, col])
                if nv is not None and (av is None or abs(nv - av) > TOLERANCIA):
                    nuevos[col] = nv
                    corregidas[col] += 1
                    cambia = True
                else:
                    nuevos[col] = av

            for col in CONTROL.values():
                if col not in api.columns:
                    continue
                nv, av = redondear(api.at[ts, col]), redondear(bd.at[ts, col])
                if nv is not None and av is not None and abs(nv - av) > TOLERANCIA:
                    alertas[col] += 1

            if cambia:
                actualizaciones.append(
                    [ts] + [nuevos.get(c) for c in AFECTADAS.values()])

        n = len(actualizaciones)
        print(f"  {ini_mes:%Y-%m}  {len(api):>4} horas API · "
              f"{len(bd):>4} en BD · {n:>4} filas a corregir")

        if n and args.ejecutar:
            cols = list(AFECTADAS.values())
            sets = ", ".join(f"{c} = v.{c}::numeric" for c in cols)
            campos = ", ".join(cols)
            sql = (f'UPDATE {TABLA} AS t SET {sets} '
                   f'FROM (VALUES %s) AS v(ts, {campos}) '
                   f'WHERE t."{tcol}" = v.ts::timestamptz')
            with conn:
                with conn.cursor() as cur:
                    execute_values(cur, sql, actualizaciones)

        mes += relativedelta(months=1)

    conn.close()

    print("\n" + "=" * 74)
    print("CELDAS CORREGIDAS")
    print("=" * 74)
    for c, n in corregidas.items():
        print(f"  {c:<28} {n:>8}")

    print("\nCONTROL (deberian ser todos 0)")
    for c, n in alertas.items():
        marca = "  <-- REVISAR" if n else ""
        print(f"  {c:<28} {n:>8}{marca}")

    if sin_fila:
        print(f"\n  {sin_fila} horas de la API sin fila en BD "
              f"(el recalculo no inserta, solo corrige)")

    if any(alertas.values()):
        print("\n  AVISO: hay diferencias en columnas que deberian estar")
        print("  limpias. Investigar antes de dar el recalculo por bueno.")

    if not args.ejecutar:
        print("\n  SIMULACION: no se ha escrito nada. Repetir con --ejecutar.")
    print("=" * 74)


if __name__ == "__main__":
    main()
