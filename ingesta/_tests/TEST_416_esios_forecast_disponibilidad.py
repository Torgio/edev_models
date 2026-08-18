"""
TEST_416 - Disponibilidad real de los indicadores de esios_forecast_da
=======================================================================

Responde a tres preguntas distintas que a simple vista se confunden:

  PASO 1  Que indicadores devuelven dato y en que aniios.
          Distingue "la serie no existe" de "la serie empieza mas tarde".

  PASO 2  Granularidad NATIVA de cada indicador (24 valores/dia = horario,
          96 = cuartohorario).
          Esto ademas CONFIRMA EL BUG x4: el pipeline pide time_trunc=hour
          sin time_agg, y el default de ESIOS es SUM. Los cuartohorarios
          salen multiplicados por 4 exacto.

  PASO 3  (opcional, --buscar-inicio) Primera fecha con dato, por biseccion.
          Sirve para decidir si un NULL es un hueco a recargar o el inicio
          real de la serie, que solo hay que documentar.

NO escribe en base de datos.

Uso
---
    python TEST_416_esios_forecast_disponibilidad.py
    python TEST_416_esios_forecast_disponibilidad.py --buscar-inicio
    python TEST_416_esios_forecast_disponibilidad.py --solo ntc
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.append(str(Path(__file__).parent.parent))

API = "https://api.esios.ree.es/indicators"
GEO_PENINSULA = 8741
PAUSA = 0.35

CREDENTIALS_CANDIDATAS = [
    Path(__file__).parent.parent / "credentials.json",
    Path.home() / "scripts" / "ingesta" / "credentials.json",
]

# ---------------------------------------------------------------------------
# VERIFICAR contra el dict INDICADORES de esios_forecast_da_pipeline.py.
# El mapeo de las seis NTC a imp/exp es el punto mas facil de equivocar.
# ---------------------------------------------------------------------------
INDICADORES = {
    1775:  "demanda_prev_mw",
    1777:  "gen_wind_prev_mw",
    1779:  "gen_solar_pv_prev_mw",
    10358: "gen_renovables_prev_mw",
    10249: "demanda_residual_prev_mw",
    1844:  "ntc_fr_imp_prev_mw",
    1845:  "ntc_fr_exp_prev_mw",
    1846:  "ntc_pt_imp_prev_mw",
    1848:  "ntc_pt_exp_prev_mw",
    1849:  "ntc_ma_imp_prev_mw",
    1850:  "ntc_ma_exp_prev_mw",
    2563:  "demanda_mercado_prev_mw",
    462:   "potencia_indisp_pbf_mw",
    543:   "gen_solartermica_prev_mw",
    570:   "cap_baleares_prev_mw",
}

FECHAS_SONDEO = [
    date(2020, 1, 15), date(2021, 1, 15), date(2022, 1, 15),
    date(2023, 1, 15), date(2024, 1, 15), date(2025, 1, 15),
    date(2026, 1, 15), date(2026, 6, 15),
]

INICIO_BUSQUEDA = date(2019, 12, 1)


def leer_token() -> str:
    if os.environ.get("ESIOS_TOKEN"):
        return os.environ["ESIOS_TOKEN"]
    for ruta in CREDENTIALS_CANDIDATAS:
        if ruta.exists():
            cred = json.loads(ruta.read_text(encoding="utf-8"))
            for c in ("x-api-key", "esios_token", "token_esios", "api_key"):
                if c in cred:
                    print(f"  Token leido de {ruta} (clave '{c}')")
                    return cred[c]
    raise SystemExit("No se encontro el token de ESIOS.")


def cabeceras(token):
    return {"Accept": "application/json; application/vnd.esios-api-v1+json",
            "Content-Type": "application/json", "x-api-key": token}


def pedir(ind: int, dia: date, token: str, agregado: bool) -> list:
    """agregado=False -> granularidad nativa. True -> horario con media."""
    params = {"start_date": f"{dia}T00:00", "end_date": f"{dia}T23:59",
              "geo_ids[]": [GEO_PENINSULA]}
    if agregado:
        params["time_trunc"] = "hour"
        params["time_agg"] = "average"
    time.sleep(PAUSA)
    r = requests.get(f"{API}/{ind}", params=params,
                     headers=cabeceras(token), timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()["indicator"]["values"]


def hay_dato(ind: int, dia: date, token: str) -> bool:
    try:
        return len(pedir(ind, dia, token, agregado=False)) > 0
    except Exception:
        return False


def buscar_inicio(ind: int, token: str) -> date | None:
    """Biseccion sobre la fecha de la primera publicacion."""
    lo, hi = INICIO_BUSQUEDA, date.today() - timedelta(days=1)
    if not hay_dato(ind, hi, token):
        return None
    if hay_dato(ind, lo, token):
        return lo
    while (hi - lo).days > 1:
        mid = lo + (hi - lo) / 2
        if hay_dato(ind, mid, token):
            hi = mid
        else:
            lo = mid
    return hi


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--buscar-inicio", action="store_true",
                   help="Biseccion de la primera fecha con dato (lento)")
    p.add_argument("--solo", type=str, default=None,
                   help="Filtra por texto en el nombre de columna")
    args = p.parse_args()

    token = leer_token()
    items = [(i, c) for i, c in INDICADORES.items()
             if not args.solo or args.solo in c]

    # ---------------- PASO 1 ------------------------------------------
    print("=" * 96)
    print("PASO 1 - Hay dato? (X = si, . = no)   columnas = "
          + " ".join(f.strftime("%y-%m") for f in FECHAS_SONDEO))
    print("=" * 96)

    presencia = {}
    for ind, col in items:
        marcas = []
        for f in FECHAS_SONDEO:
            marcas.append("X" if hay_dato(ind, f, token) else ".")
        presencia[ind] = marcas
        print(f"  {ind:<6} {col:<26} {'  '.join(marcas)}")

    # ---------------- PASO 2 ------------------------------------------
    print("\n" + "=" * 96)
    print("PASO 2 - Granularidad nativa y efecto del bug x4  (dia 2026-06-15)")
    print("=" * 96)
    print(f"  {'ID':<6} {'columna':<26} {'nativos':>8} {'tipo':>14} "
          f"{'sin time_agg':>13} {'con average':>12} {'ratio':>7}")

    dia = date(2026, 6, 15)
    for ind, col in items:
        if presencia[ind][-1] == ".":
            print(f"  {ind:<6} {col:<26} {'sin dato ese dia':>50}")
            continue
        try:
            nativos = pedir(ind, dia, token, agregado=False)
            n = len(nativos)
            tipo = ("horario" if n <= 24 else
                    "CUARTOHORARIO" if n <= 96 else "5-min")

            # lo que hace hoy el pipeline: time_trunc=hour sin time_agg
            time.sleep(PAUSA)
            r = requests.get(f"{API}/{ind}", headers=cabeceras(token),
                             timeout=45,
                             params={"start_date": f"{dia}T00:00",
                                     "end_date": f"{dia}T23:59",
                                     "geo_ids[]": [GEO_PENINSULA],
                                     "time_trunc": "hour"})
            sin_agg = r.json()["indicator"]["values"]
            con_agg = pedir(ind, dia, token, agregado=True)

            v_sin = sin_agg[0]["value"] if sin_agg else None
            v_con = con_agg[0]["value"] if con_agg else None
            ratio = (v_sin / v_con) if (v_sin and v_con) else None

            print(f"  {ind:<6} {col:<26} {n:>8} {tipo:>14} "
                  f"{('-' if v_sin is None else f'{v_sin:.2f}'):>13} "
                  f"{('-' if v_con is None else f'{v_con:.2f}'):>12} "
                  f"{('-' if ratio is None else f'{ratio:.3f}'):>7}")
        except Exception as e:
            print(f"  {ind:<6} {col:<26} ERROR {str(e)[:40]}")

    print("\n  ratio ~4.000 -> la columna esta INFLADA x4 en BD, hay que")
    print("  recalcular SOBRESCRIBIENDO (no basta rellenar nulls).")
    print("  ratio ~1.000 -> nativo horario, no afectado.")

    # ---------------- PASO 3 ------------------------------------------
    if args.buscar_inicio:
        print("\n" + "=" * 96)
        print("PASO 3 - Primera fecha con dato (biseccion)")
        print("=" * 96)
        for ind, col in items:
            ini = buscar_inicio(ind, token)
            txt = ini.isoformat() if ini else "SIN DATO EN NINGUNA FECHA"
            print(f"  {ind:<6} {col:<26} {txt}")
        print("\n  Un NULL anterior a esa fecha NO es un hueco: es que la")
        print("  serie no existia. Se documenta, no se recarga.")

    print("\n  Este script no ha escrito nada en base de datos.")


if __name__ == "__main__":
    main()
