"""
entsoe_forecast_da_pipeline.py — carga diaria de previsiones day-ahead ENTSO-E
==============================================================================

Objetivo: el dia D+1 completo, mas una ventana de revision hacia atras.

Por que la ventana de revision: ENTSO-E REPUBLICA. Verificado el 15/08/2026,
la prevision de demanda del dia 16 cambio de 22.332 a 22.295 MW de minimo entre
dos consultas separadas por horas. Ademas las series se publican ESCALONADAS a
lo largo del D-1 (demanda y NTC pronto; A69 hacia las 18:00 CET; posicion neta,
intercambios y precio tras la casacion), asi que una sola pasada nunca captura
todo. La escritura usa COALESCE: un fallo puntual de la API deja la columna a
NULL y el siguiente pase la rellena, sin machacar lo ya bueno.

AGREGACION: todo se reduce a horario con resample("h").mean(). Son MW y
EUR/MWh — magnitudes intensivas. NUNCA sum. Las NTC ya vienen horarias.

SOLO PREVISIONES. Las columnas de PROGRAMA (gen/cons scheduled, posicion neta,
saldos programados) se eliminaron el 15/08/2026: no son prevision y ya estan,
con desglose por tecnologia, en esios_pbf_gen y esios_pbf_load_inter.

Uso
---
    python entsoe_forecast_da_pipeline.py                # D+1 + revision 7 dias
    python entsoe_forecast_da_pipeline.py --dia 2026-06-15
    python entsoe_forecast_da_pipeline.py --desde 2026-06-01 --hasta 2026-06-30
    python entsoe_forecast_da_pipeline.py --revision 14
    python entsoe_forecast_da_pipeline.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent))

try:
    from entsoe import EntsoePandasClient
    from entsoe.exceptions import NoMatchingDataError
except ImportError:
    raise SystemExit("Falta entsoe-py:  pip install entsoe-py")

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

TZ = "Europe/Madrid"
PAIS = "ES"
TABLA = "entsoe_forecast_da"

REVISION_DIAS = 7
PAUSA_API = 0.4          # cortesia entre peticiones

CREDENTIALS_CANDIDATAS = [
    Path(__file__).parent / "credentials.json",
    Path(__file__).parent.parent / "credentials.json",
    Path.home() / "scripts" / "ingesta" / "credentials.json",
]

COLS = [
    "load_forecast_mw",
    "wind_forecast_mw",
    "solar_forecast_mw",
    "ntc_fr_mw",
    "ntc_pt_mw",
    "price_fr_eur_mwh",
]
# renewables_forecast_mw NO se lista: es GENERATED, la calcula PostgreSQL.

INSERT_SQL = f"""
INSERT INTO {TABLA} (datetime, {', '.join(COLS)})
VALUES %s
ON CONFLICT (datetime) DO UPDATE SET
{', '.join(f'{c} = COALESCE(EXCLUDED.{c}, {TABLA}.{c})' for c in COLS)}
"""


def leer_credenciales() -> dict:
    for ruta in CREDENTIALS_CANDIDATAS:
        if ruta.exists():
            return json.loads(ruta.read_text(encoding="utf-8"))
    raise SystemExit("No se encontro credentials.json")


def token_entsoe(cred: dict) -> str:
    if os.environ.get("ENTSOE_TOKEN"):
        return os.environ["ENTSOE_TOKEN"]
    for c in ("entsoe_token", "token_entsoe", "entsoe_api_key"):
        if c in cred:
            return cred[c]
    raise SystemExit("No hay token de ENTSO-E en credentials.json")


def conectar(cred: dict):
    return psycopg2.connect(
        host=cred["db_host"], port=cred.get("db_port", 5432),
        dbname=cred["db_name"], user=cred["db_user"],
        password=cred["db_password"],
    )


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

def a_horario(obj, columna=None) -> pd.Series | None:
    """Normaliza a Series horaria. MW/EUR-MWh -> media, nunca suma."""
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        if obj.empty:
            return None
        if columna is not None:
            if columna not in obj.columns:
                return None
            s = obj[columna]
        else:
            s = obj.iloc[:, 0]
    else:
        s = obj
    if s is None or len(s) == 0:
        return None

    s = s.astype(float)
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    s.index = s.index.tz_convert(TZ)
    return s.resample("h").mean()


def intentar(nombre: str, fn):
    """Ejecuta una consulta tolerando que aun no este publicada."""
    try:
        time.sleep(PAUSA_API)
        return fn()
    except NoMatchingDataError:
        print(f"    {nombre}: sin publicar todavia")
        return None
    except Exception as e:
        print(f"    {nombre}: ERROR {type(e).__name__} {str(e)[:80]}")
        return None


def descargar_dia(client, dia: date) -> pd.DataFrame:
    """Devuelve un DataFrame horario con las columnas de COLS que haya."""
    ini = pd.Timestamp(dia, tz=TZ)
    fin = ini + pd.Timedelta(days=1)

    datos: dict[str, pd.Series] = {}

    def guardar(col, serie):
        if serie is not None and len(serie):
            datos[col] = serie

    guardar("load_forecast_mw", a_horario(
        intentar("demanda", lambda: client.query_load_forecast(
            PAIS, start=ini, end=fin)), "Forecasted Load"))

    ren = intentar("eolica/solar A69",
                   lambda: client.query_wind_and_solar_forecast(
                       PAIS, start=ini, end=fin, psr_type=None))
    guardar("wind_forecast_mw", a_horario(ren, "Wind Onshore"))
    guardar("solar_forecast_mw", a_horario(ren, "Solar"))

    for col, vecino in (("ntc_fr_mw", "FR"), ("ntc_pt_mw", "PT")):
        guardar(col, a_horario(
            intentar(f"NTC {vecino}",
                     lambda v=vecino: client.query_net_transfer_capacity_dayahead(
                         PAIS, v, start=ini, end=fin))))

    guardar("price_fr_eur_mwh", a_horario(
        intentar("precio FR", lambda: client.query_day_ahead_prices(
            "FR", start=ini, end=fin))))

    if not datos:
        return pd.DataFrame()

    df = pd.DataFrame(datos)

    # Recorte al dia solicitado. El precio devuelve 97 valores (incluye la
    # medianoche del dia siguiente); sin este filtro se duplicaria la primera
    # hora al cargar dias consecutivos.
    df = df[(df.index >= ini) & (df.index < fin)]

    for c in COLS:
        if c not in df.columns:
            df[c] = None
    return df[COLS].round(2)


# ---------------------------------------------------------------------------

def escribir(conn, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    filas = [
        [idx.to_pydatetime()] + [None if pd.isna(v) else float(v)
                                 for v in fila]
        for idx, fila in zip(df.index, df.to_numpy())
    ]
    with conn:
        with conn.cursor() as cur:
            execute_values(cur, INSERT_SQL, filas)
    return len(filas)


def main() -> None:
    hoy = date.today()
    p = argparse.ArgumentParser()
    p.add_argument("--dia", type=date.fromisoformat,
                   help="Un unico dia (desactiva la ventana de revision)")
    p.add_argument("--desde", type=date.fromisoformat)
    p.add_argument("--hasta", type=date.fromisoformat)
    p.add_argument("--revision", type=int, default=REVISION_DIAS)
    p.add_argument("--dry-run", action="store_true",
                   help="Descarga y muestra, no escribe en BD")
    args = p.parse_args()

    if args.dia:
        dias = [args.dia]
    elif args.desde and args.hasta:
        dias = pd.date_range(args.desde, args.hasta, freq="D").date.tolist()
    else:
        # objetivo D+1 y revision de los ultimos N dias por republicacion
        dias = [hoy + timedelta(days=1)]
        dias += [hoy - timedelta(days=k) for k in range(0, args.revision)]
        dias = sorted(set(dias))

    cred = leer_credenciales()
    client = EntsoePandasClient(api_key=token_entsoe(cred))
    conn = None if args.dry_run else conectar(cred)

    print("=" * 70)
    print(f"{TABLA} — {len(dias)} dias: {dias[0]} .. {dias[-1]}"
          + ("   [DRY RUN]" if args.dry_run else ""))
    print("=" * 70)

    total = 0
    for d in dias:
        print(f"\n[{d}]")
        df = descargar_dia(client, d)
        if df.empty:
            print("    nada descargado")
            continue

        cubiertas = [c for c in COLS if df[c].notna().any()]
        print(f"    {len(df)} horas · {len(cubiertas)}/{len(COLS)} columnas "
              f"con dato")
        faltan = [c for c in COLS if c not in cubiertas]
        if faltan:
            print(f"    sin dato: {', '.join(faltan)}")

        if args.dry_run:
            print(df.head(3).to_string())
        else:
            total += escribir(conn, df)

    if conn:
        conn.close()
    print(f"\n{'=' * 70}\nFilas escritas: {total}\n{'=' * 70}")


if __name__ == "__main__":
    main()
