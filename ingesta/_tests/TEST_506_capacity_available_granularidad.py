"""
TEST_506_capacity_available_granularidad.py
===========================================

Tres preguntas sobre los indicadores de potencia DISPONIBLE (472-478), que hoy
se cargan con time_trunc=day en esios_capacity_available:

  1. GRANULARIDAD. ¿Existe resolucion horaria real, o ESIOS replica el valor
     diario 24 veces? Si los 24 valores de un dia son identicos, subir a
     horario no aporta informacion, solo multiplica por 24 el peso de la tabla.

  2. HORIZONTE D+1. ¿Publican para mañana? La tabla actual es potencia
     disponible REAL, publicada a posteriori: como feature de prediccion tiene
     el mismo problema que generation, es informacion del dia D y no anterior
     al cierre de las 12:00 de D-1. Si hay dato para D+1, la columna pasa de
     ser una consecuencia a ser un predictor legitimo.

  3. REVISIONES. ¿ESIOS corrige dias ya publicados? El pipeline actual tiene
     REVISAR_EXISTENTES=False, asi que omite los dias ya cargados sin llamar a
     la API y una correccion nunca entra. Esto compara lo que hay en la BD con
     lo que devuelve la API hoy para los mismos dias.

NO ESCRIBE EN LA BD. Solo lee y compara.

USO
    python TEST_506_capacity_available_granularidad.py
    python TEST_506_capacity_available_granularidad.py --dia 2026-08-15
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg2
import requests

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

BASE = "https://api.esios.ree.es/indicators"
PENINSULA = 8741
TABLA = "esios_capacity_available"

INDICADORES = {
    472: "hydro_mw",
    473: "pump_mw",
    474: "nuclear_mw",
    475: "coal_antracita_mw",
    476: "coal_subbituminosa_mw",
    477: "ccgt_mw",
    478: "fuel_mw",
}

# Columnas tal como estan en la tabla (coal va sumada)
COLS_BD = ["hydro_mw", "pump_mw", "nuclear_mw", "coal_mw", "ccgt_mw", "fuel_mw"]


def pedir(headers, ind_id: int, dia: date, time_trunc: str) -> pd.DataFrame:
    """Devuelve las filas de Peninsula para un dia, con la granularidad dada."""
    params = {
        "start_date": f"{dia}T00:00:00",
        "end_date":   f"{dia}T23:59:59",
        "time_trunc": time_trunc,
        "time_agg":   "avg",
        "geo_agg":    "sum",
        "geo_trunc":  "electric_system",
    }
    try:
        r = requests.get(f"{BASE}/{ind_id}", headers=headers,
                         params=params, timeout=30)
        if r.status_code != 200:
            return pd.DataFrame()
        vals = r.json().get("indicator", {}).get("values", [])
        if not vals:
            return pd.DataFrame()
        df = pd.json_normalize(vals)
        if "geo_id" not in df.columns:
            return pd.DataFrame()
        return df[df["geo_id"] == PENINSULA]
    except Exception as e:
        print(f"    ind {ind_id}: ERROR {str(e)[:70]}")
        return pd.DataFrame()


# ── 1. Granularidad ───────────────────────────────────────────────────────────

def test_granularidad(headers, dia: date):
    print("=" * 74)
    print(f"1. GRANULARIDAD — dia {dia}")
    print("=" * 74)
    print(f"{'indicador':<26} {'n_day':>6} {'n_hour':>7} {'distintos':>10} "
          f"{'min':>10} {'max':>10}")
    print("-" * 74)

    algun_horario = False
    for ind_id, nombre in INDICADORES.items():
        d_day  = pedir(headers, ind_id, dia, "day")
        time.sleep(0.3)
        d_hour = pedir(headers, ind_id, dia, "hour")
        time.sleep(0.3)

        n_day  = len(d_day)
        n_hour = len(d_hour)
        if n_hour:
            vals = d_hour["value"].round(2)
            distintos = vals.nunique()
            lo, hi = vals.min(), vals.max()
            if distintos > 1:
                algun_horario = True
        else:
            distintos, lo, hi = 0, float("nan"), float("nan")

        print(f"{nombre:<26} {n_day:>6} {n_hour:>7} {distintos:>10} "
              f"{lo:>10.1f} {hi:>10.1f}")

    print()
    if algun_horario:
        print("=> HAY variacion intradiaria: la resolucion horaria es real y")
        print("   subir la tabla a horario SI aporta informacion.")
    else:
        print("=> Sin variacion intradiaria: ESIOS replica el valor diario en")
        print("   las 24 horas. Subir a horario multiplicaria x24 el peso de la")
        print("   tabla sin añadir ni un bit de informacion. Mantener day.")
    print()


# ── 2. Horizonte D+1 ──────────────────────────────────────────────────────────

def test_horizonte(headers):
    hoy = date.today()
    print("=" * 74)
    print("2. HORIZONTE — ¿hasta que dia publican?")
    print("=" * 74)
    print(f"{'dia':<14} {'offset':>7}  " +
          "  ".join(f"{n[:9]:>9}" for n in INDICADORES.values()))
    print("-" * 74)

    for k in (-2, -1, 0, 1, 2):
        dia = hoy + timedelta(days=k)
        etiqueta = {0: "HOY", 1: "D+1", 2: "D+2"}.get(k, f"D{k}")
        celdas = []
        for ind_id in INDICADORES:
            df = pedir(headers, ind_id, dia, "day")
            time.sleep(0.25)
            celdas.append(f"{df['value'].iloc[-1]:>9.0f}" if len(df)
                          else f"{'--':>9}")
        print(f"{str(dia):<14} {etiqueta:>7}  " + "  ".join(celdas))

    print()
    print("=> Si hay dato en D+1, la potencia disponible deja de ser una")
    print("   consecuencia publicada a posteriori y pasa a ser un predictor")
    print("   legitimo: capacidad termica declarada antes del cierre del")
    print("   mercado. Ojo: habria que confirmar la HORA de publicacion, no")
    print("   basta con que el dato exista al consultarlo por la tarde.")
    print()


# ── 3. Revisiones ─────────────────────────────────────────────────────────────

def test_revisiones(headers, db_config, n_dias: int = 10):
    print("=" * 74)
    print(f"3. REVISIONES — BD frente a API, ultimos {n_dias} dias")
    print("=" * 74)

    hoy = date.today()
    desde = hoy - timedelta(days=n_dias)

    conn = psycopg2.connect(**db_config)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT date, {', '.join(COLS_BD)} FROM {TABLA} "
            f"WHERE date >= %s ORDER BY date", (desde,))
        filas = cur.fetchall()
    conn.close()

    if not filas:
        print("Sin filas en la BD para ese rango.")
        return

    print(f"{'dia':<14} {'columna':<20} {'en BD':>12} {'en API':>12} {'dif':>10}")
    print("-" * 74)

    n_dif = 0
    for fila in filas:
        dia = fila[0]
        en_bd = dict(zip(COLS_BD, fila[1:]))

        # coal se guarda sumada: hay que recomponerla igual que el pipeline
        api = {}
        for ind_id, nombre in INDICADORES.items():
            df = pedir(headers, ind_id, dia, "day")
            time.sleep(0.25)
            api[nombre] = round(float(df["value"].iloc[-1]), 2) if len(df) else None

        coal = [api.get("coal_antracita_mw"), api.get("coal_subbituminosa_mw")]
        coal = [c for c in coal if c is not None]
        api["coal_mw"] = round(sum(coal), 2) if coal else None

        for col in COLS_BD:
            v_bd, v_api = en_bd.get(col), api.get(col)
            if v_bd is None or v_api is None:
                continue
            dif = float(v_api) - float(v_bd)
            if abs(dif) > 0.01:
                n_dif += 1
                print(f"{str(dia):<14} {col:<20} {float(v_bd):>12.2f} "
                      f"{v_api:>12.2f} {dif:>+10.2f}")

    print("-" * 74)
    if n_dif:
        print(f"=> {n_dif} valores han cambiado en la API respecto a lo que hay")
        print("   almacenado. Con REVISAR_EXISTENTES=False esas correcciones NO")
        print("   entran nunca: el pipeline omite los dias ya presentes sin")
        print("   llamar a la API. Hay que ponerlo a True y acotar DIAS_ATRAS.")
    else:
        print("=> Ninguna discrepancia. ESIOS no ha revisado estos dias, o las")
        print("   revisiones caen fuera de la ventana probada. Repetir con mas")
        print("   dias antes de concluir que no revisa nunca.")
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dia", type=date.fromisoformat,
                   help="Dia para el test de granularidad (default: anteayer)")
    p.add_argument("--revision-dias", type=int, default=10)
    args = p.parse_args()

    dia = args.dia or (date.today() - timedelta(days=2))
    headers, db_config = load_config()

    print(f"\nTEST 506 — potencia disponible, granularidad y horizonte")
    print(f"{datetime.now():%Y-%m-%d %H:%M}\n")

    test_granularidad(headers, dia)
    test_horizonte(headers)
    test_revisiones(headers, db_config, args.revision_dias)


if __name__ == "__main__":
    main()
