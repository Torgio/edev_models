#!/usr/bin/env python3
"""
TEST_417 - Sonda de indicadores de esios_forecast_da.

Responde tres preguntas, por indicador, sin tocar la BD:

  1. GRANULARIDAD NATIVA: cuantos puntos publica ESIOS en un dia
     (24 = horario, 96 = cuartohorario, 288 = 5 min).
  2. FACTOR DE INFLACION: cociente entre el valor devuelto con
     `time_trunc=hour` SIN time_agg (ESIOS agrega por SUM) y el mismo
     valor CON `time_agg=average`. Si el cociente es 1.0 el indicador
     es horario nativo y el bug del x4 no le afecta; si es 4.0 hay que
     recalcular esa columna.
  3. GEO_IDS DISPONIBLES: se consulta sin filtro geografico y se listan
     los geo_id presentes en la respuesta. Resuelve el geo_id pendiente
     del indicador 570 (Baleares) sin adivinarlo.

Uso:
    python TEST_417_forecast_da_indicadores.py
    python TEST_417_forecast_da_indicadores.py --fecha 2026-06-15
    python TEST_417_forecast_da_indicadores.py --indicador 570 --geo-detalle

Se elige por defecto un dia SIN cambio de hora y ya publicado.
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import requests

CREDENTIALS = Path("/home/ubuntu/scripts/ingesta/credentials.json")
BASE_URL = "https://api.esios.ree.es/indicators"
GEO_PENINSULA = 8741

# Indicadores que hay (o habra) en esios_forecast_da.
# nombre_columna: (indicador, descripcion)
INDICADORES = {
    "demanda_prev_mw":            (1775, "Demanda prevista D+1 (Circular 4/2019)"),
    "demanda_mercado_prev_mw":    (2563, "Demanda prevista mercado (sin autoconsumo) - NUEVA"),
    "demanda_prev_544_mw":        (544,  "Prevision demanda diaria (serie antigua)"),
    "gen_eolica_prev_mw":         (1777, "Prevision eolica D+1 (Circular 4/2019) - CANONICA?"),
    "gen_solar_prev_mw":          (1779, "Prevision solar fotovoltaica D+1 (Circular 4/2019)"),
    "gen_solar_542_mw":           (542,  "Prevision fotovoltaica (serie antigua)"),
    "gen_solartermica_prev_mw":   (543,  "Prevision solar termica D+1"),
    "gen_renovables_prev_mw":     (10358, "Prevision renovable total D+1"),
    "demanda_residual_prev_mw":   (10249, "Demanda residual prevista (RIESGO DE FUGA)"),
    "potencia_indisp_pbf_mw":     (462,  "Indisponibilidad de generacion en PBF - NUEVA"),
    "cap_baleares_prev_mw":       (570,  "Prevision enlace Peninsula-Baleares - NUEVA, geo_id?"),
    "ntc_fr_imp_prev_mw":         (1844, "NTC prevista importacion Francia"),
    "ntc_fr_exp_prev_mw":         (1845, "NTC prevista exportacion Francia"),
    "ntc_pt_imp_prev_mw":         (1846, "NTC prevista importacion Portugal"),
    "ntc_pt_exp_prev_mw":         (1848, "NTC prevista exportacion Portugal"),
    "ntc_ma_imp_prev_mw":         (1849, "NTC prevista importacion Marruecos"),
    "ntc_ma_exp_prev_mw":         (1850, "NTC prevista exportacion Marruecos"),
}

# Series H+3: NO deben acabar en la tabla de features (fuga de datos).
# Se sondean solo para dejar constancia documental del contraste.
INDICADORES_H3 = {
    "H+3 demanda":   1776,
    "H+3 eolica":    1778,
    "H+3 solar":     1780,
    "H+3 renovable": 10359,
}


def cargar_token():
    """El token de ESIOS puede estar bajo varias claves segun el fichero."""
    if not CREDENTIALS.exists():
        sys.exit(f"ERROR: no existe {CREDENTIALS}")
    with open(CREDENTIALS) as f:
        creds = json.load(f)
    for clave in ("esios_token", "token_esios", "esios_api_key", "token", "api_key"):
        if creds.get(clave):
            return creds[clave]
    sys.exit(f"ERROR: no encuentro el token de ESIOS. Claves disponibles: {list(creds)}")


def headers(token):
    return {
        "Accept": "application/json; application/vnd.esios-api-v2+json",
        "Content-Type": "application/json",
        "x-api-key": token,
    }


def pedir(indicador, dia, token, *, time_trunc=None, time_agg=None, geo_id=None):
    """Una llamada a la API. Devuelve la lista de valores o None si falla."""
    params = {
        "start_date": f"{dia}T00:00:00+02:00",
        "end_date":   f"{dia}T23:59:59+02:00",
    }
    if time_trunc:
        params["time_trunc"] = time_trunc
    if time_agg:
        params["time_agg"] = time_agg
    if geo_id:
        params["geo_ids[]"] = geo_id
        params["geo_trunc"] = "electric_system"
        params["geo_agg"] = "sum"

    try:
        r = requests.get(f"{BASE_URL}/{indicador}", params=params,
                         headers=headers(token), timeout=30)
    except requests.RequestException as e:
        return None, f"excepcion: {e}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        return r.json()["indicator"]["values"], None
    except (KeyError, ValueError) as e:
        return None, f"respuesta inesperada: {e}"


def valor_a_mediodia(valores):
    """Valor de la hora 12 local, la mas robusta (sin bordes de dia ni DST)."""
    for v in valores:
        if v["datetime"][11:13] == "12":
            return v["value"]
    return None


def sondear(nombre, indicador, desc, dia, token):
    fila = {"columna": nombre, "ind": indicador, "desc": desc}

    # --- 1. Granularidad nativa: sin time_trunc, sin time_agg ---
    crudo, err = pedir(indicador, dia, token, geo_id=GEO_PENINSULA)
    if crudo is None:
        fila["estado"] = f"FALLO ({err})"
        return fila
    if not crudo:
        # Reintento sin filtro geografico: puede no existir en peninsula.
        crudo, err = pedir(indicador, dia, token)
        if not crudo:
            fila["estado"] = "SIN DATOS"
            return fila
        fila["nota"] = "solo fuera de geo 8741"

    n = len(crudo)
    fila["n_puntos"] = n
    fila["nativa"] = {24: "horaria", 96: "cuartohoraria",
                      288: "5 min", 1: "diaria"}.get(n, f"{n} puntos")

    # geo_ids presentes (resuelve el geo_id de 570)
    geos = Counter(v.get("geo_id") for v in crudo if v.get("geo_id") is not None)
    if geos:
        fila["geo_ids"] = ", ".join(f"{g}({c})" for g, c in geos.most_common(4))

    # --- 2. Factor de inflacion en la agregacion horaria ---
    sin_agg, _ = pedir(indicador, dia, token,
                       time_trunc="hour", geo_id=GEO_PENINSULA)
    con_agg, _ = pedir(indicador, dia, token, time_trunc="hour",
                       time_agg="average", geo_id=GEO_PENINSULA)

    v_sin = valor_a_mediodia(sin_agg) if sin_agg else None
    v_con = valor_a_mediodia(con_agg) if con_agg else None
    fila["v_sum"] = v_sin
    fila["v_avg"] = v_con

    if v_sin is not None and v_con not in (None, 0):
        ratio = v_sin / v_con
        fila["ratio"] = round(ratio, 3)
        if abs(ratio - 1) < 0.01:
            fila["estado"] = "OK - horario nativo, no requiere recalculo"
        elif abs(ratio - 4) < 0.05:
            fila["estado"] = "x4 - RECALCULO OBLIGATORIO"
        elif ratio > 4:
            fila["estado"] = f"x{ratio:.1f} - RECALCULO OBLIGATORIO"
        else:
            fila["estado"] = f"ratio anomalo {ratio:.3f} - revisar a mano"
    else:
        fila["estado"] = "no concluyente (falta valor a las 12:00)"

    return fila


def imprimir(filas):
    print()
    print("=" * 108)
    print(f"{'columna':<28} {'ind':>6} {'nativa':<15} {'v_SUM':>12} {'v_AVG':>12} {'ratio':>7}  estado")
    print("-" * 108)
    for f in filas:
        vs = f"{f['v_sum']:,.1f}" if isinstance(f.get("v_sum"), (int, float)) else "-"
        va = f"{f['v_avg']:,.1f}" if isinstance(f.get("v_avg"), (int, float)) else "-"
        print(f"{f['columna']:<28} {f['ind']:>6} {str(f.get('nativa','-')):<15} "
              f"{vs:>12} {va:>12} {str(f.get('ratio','-')):>7}  {f['estado']}")
    print("=" * 108)

    print("\nGEO_IDS DETECTADOS (relevante para el indicador 570):")
    for f in filas:
        if f.get("geo_ids"):
            print(f"  {f['ind']:>6}  {f['columna']:<28} -> {f['geo_ids']}")
        if f.get("nota"):
            print(f"  {f['ind']:>6}  {f['columna']:<28} !! {f['nota']}")

    recalculo = [f for f in filas if "RECALCULO" in f["estado"]]
    limpios = [f for f in filas if f["estado"].startswith("OK")]
    problemas = [f for f in filas
                 if f["estado"].startswith(("FALLO", "SIN DATOS", "no concluyente", "ratio anomalo"))]

    print(f"\nRESUMEN")
    print(f"  Columnas a recalcular : {len(recalculo)}")
    for f in recalculo:
        print(f"      - {f['columna']} ({f['ind']})")
    print(f"  Columnas ya correctas : {len(limpios)}")
    for f in limpios:
        print(f"      - {f['columna']} ({f['ind']})")
    if problemas:
        print(f"  Requieren atencion    : {len(problemas)}")
        for f in problemas:
            print(f"      - {f['columna']} ({f['ind']}): {f['estado']}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fecha", help="YYYY-MM-DD. Por defecto: hace 10 dias")
    p.add_argument("--indicador", type=int, help="sondear solo este indicador")
    p.add_argument("--h3", action="store_true",
                   help="sondear tambien las series H+3 (constancia documental)")
    p.add_argument("--pausa", type=float, default=0.4,
                   help="segundos entre llamadas (evitar 403 por rate limit)")
    args = p.parse_args()

    dia = args.fecha or str(date.today() - timedelta(days=10))
    # Aviso de DST: los dias de cambio de hora distorsionan el conteo de puntos.
    d = date.fromisoformat(dia)
    if d.month in (3, 10) and d.day >= 25:
        print(f"AVISO: {dia} puede ser dia de cambio de hora. "
              f"El conteo de puntos no sera 24/96/288. Usa otra fecha.")

    token = cargar_token()
    print(f"TEST_417 - sonda de indicadores | dia de referencia: {dia}")
    print(f"Criterio: ratio = valor(SUM) / valor(AVG) a las 12:00 local, geo_id={GEO_PENINSULA}")

    objetivo = INDICADORES
    if args.indicador:
        objetivo = {k: v for k, v in INDICADORES.items() if v[0] == args.indicador}
        if not objetivo:
            objetivo = {f"ind_{args.indicador}": (args.indicador, "ad hoc")}

    filas = []
    for nombre, (ind, desc) in objetivo.items():
        filas.append(sondear(nombre, ind, desc, dia, token))
        time.sleep(args.pausa)

    imprimir(filas)

    if args.h3:
        print("\nSERIES H+3 (NO usar como features - fuga de datos):")
        for nombre, ind in INDICADORES_H3.items():
            f = sondear(nombre, ind, "H+3", dia, token)
            print(f"  {ind:>6}  {nombre:<16} {str(f.get('nativa','-')):<15} "
                  f"ratio={f.get('ratio','-')}  {f['estado']}")
            time.sleep(args.pausa)


if __name__ == "__main__":
    main()
