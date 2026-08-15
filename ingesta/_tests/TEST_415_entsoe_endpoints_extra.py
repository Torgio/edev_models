"""
TEST_415 - Endpoints ENTSO-E fuera del esquema actual de entsoe_forecast_da
===========================================================================

El TEST_414 confirmo que 10 de las 12 columnas del esquema se pueden rellenar
(hidraulica y nuclear no existen como prevision day-ahead).

Este script explora lo que ENTSO-E publica ADEMAS de eso, buscando variables
que puedan aportar al modelo de precio. Interes principal:

  - INDISPONIBILIDADES de grupos de generacion: se publican con antelacion
    (pasan el filtro de no-leakage) y explican picos de precio que un modelo
    con solo demanda y renovables no anticipa.
  - RESERVAS HIDRAULICAS: nivel de embalses, semanal. Determinante del precio
    en el sistema espaniol y ausente de toda la BD actual.
  - PRECIOS de mercados acoplados (PT, DE, IT) para spreads.

Cada prueba lleva su propia ventana temporal porque las granularidades van de
cuartohoraria a semanal.

NO escribe en base de datos.

Uso
---
    python TEST_415_entsoe_endpoints_extra.py
    python TEST_415_entsoe_endpoints_extra.py --dia 2026-06-15
    python TEST_415_entsoe_endpoints_extra.py --solo indispon
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

try:
    from entsoe import EntsoePandasClient
except ImportError:
    raise SystemExit("Falta entsoe-py:  pip install entsoe-py")

TZ = "Europe/Madrid"
PAIS = "ES"

CREDENTIALS_CANDIDATAS = [
    Path(__file__).parent.parent / "credentials.json",
    Path.home() / "scripts" / "ingesta" / "credentials.json",
]


def leer_token() -> str:
    token = os.environ.get("ENTSOE_TOKEN")
    if token:
        return token
    for ruta in CREDENTIALS_CANDIDATAS:
        if ruta.exists():
            cred = json.loads(ruta.read_text(encoding="utf-8"))
            for c in ("entsoe_token", "token_entsoe", "entsoe_api_key"):
                if c in cred:
                    print(f"  Token leido de {ruta} (clave '{c}')")
                    return cred[c]
    raise SystemExit("No se encontro el token de ENTSO-E.")


# ---------------------------------------------------------------------------
# Pruebas: (clave, titulo, dias_ventana, funcion, comentario)
#
# dias_ventana: cuantos dias hacia atras desde --dia necesita la consulta.
#   1  -> series intradiarias
#   14 -> indisponibilidades (se publican con antelacion variable)
#   90 -> series semanales (embalses)
# ---------------------------------------------------------------------------

def construir_pruebas():
    return [
        # ---- Indisponibilidades -------------------------------------------
        ("indispon_gen",
         "Indisponibilidad de UNIDADES DE GENERACION",
         14,
         lambda c, i, f: c.query_unavailability_of_generation_units(
             PAIS, start=i, end=f),
         "Paradas de grupo. Publicadas con antelacion -> valido como feature."),

        ("indispon_prod",
         "Indisponibilidad de UNIDADES DE PRODUCCION",
         14,
         lambda c, i, f: c.query_unavailability_of_production_units(
             PAIS, start=i, end=f),
         "Variante agregada por central."),

        ("indispon_red_fr",
         "Indisponibilidad de RED ES-FR",
         14,
         lambda c, i, f: c.query_unavailability_transmission(
             PAIS, "FR", start=i, end=f),
         "Afecta a la NTC efectiva."),

        # ---- Hidraulica ----------------------------------------------------
        ("embalses",
         "RESERVAS HIDRAULICAS (nivel de embalses, semanal)",
         90,
         lambda c, i, f: c.query_aggregate_water_reservoirs_and_hydro_storage(
             PAIS, start=i, end=f),
         "Determinante del precio y ausente de toda la BD actual."),

        # ---- Precios de mercados acoplados ---------------------------------
        ("precio_pt",
         "Precio day-ahead PORTUGAL",
         1,
         lambda c, i, f: c.query_day_ahead_prices("PT", start=i, end=f),
         "MIBEL: deberia coincidir con ES salvo separacion de mercado."),

        ("precio_de",
         "Precio day-ahead ALEMANIA-LUX",
         1,
         lambda c, i, f: c.query_day_ahead_prices("DE_LU", start=i, end=f),
         "Referencia continental."),

        ("precio_it",
         "Precio day-ahead ITALIA NORTE",
         1,
         lambda c, i, f: c.query_day_ahead_prices("IT_NORD", start=i, end=f),
         "Referencia mediterranea."),

        # ---- Flujos --------------------------------------------------------
        ("flujo_fisico_fr",
         "FLUJOS FISICOS ES->FR (realizado)",
         1,
         lambda c, i, f: c.query_crossborder_flows(PAIS, "FR", start=i, end=f),
         "Lo realizado frente a lo programado. Es dato ex-post."),

        ("flujo_fisico_pt",
         "FLUJOS FISICOS ES->PT (realizado)",
         1,
         lambda c, i, f: c.query_crossborder_flows(PAIS, "PT", start=i, end=f),
         "Idem frontera portuguesa."),

        # ---- Parque --------------------------------------------------------
        ("capacidad_por_unidad",
         "Capacidad instalada POR UNIDAD (central a central)",
         1,
         lambda c, i, f: c.query_installed_generation_capacity_per_unit(
             PAIS, start=i, end=f),
         "Permite cruzar indisponibilidades con potencia afectada."),

        # ---- Balance -------------------------------------------------------
        ("precios_desvios",
         "Precios de DESVIOS (imbalance)",
         1,
         lambda c, i, f: c.query_imbalance_prices(PAIS, start=i, end=f),
         "Relevante para valorar el riesgo de la bateria, no para el diario."),

        ("reserva_precio",
         "Precio de RESERVA contratada (A01)",
         1,
         lambda c, i, f: c.query_contracted_reserve_prices(
             PAIS, start=i, end=f, type_marketagreement_type="A01"),
         "Banda secundaria: mercado alternativo para la bateria."),

        # ---- Leakage: no usar como feature del diario ----------------------
        ("intradiario_ren",
         "Prevision INTRADIARIA eolica+solar",
         1,
         lambda c, i, f: c.query_intraday_wind_and_solar_forecast(
             PAIS, start=i, end=f),
         "LEAKAGE para el diario. Solo util para modelos intradiarios."),
    ]


# ---------------------------------------------------------------------------

def describir(obj) -> str:
    if obj is None:
        return "None"
    if isinstance(obj, pd.Series):
        obj = obj.to_frame(name=obj.name or "valor")
    if not isinstance(obj, pd.DataFrame) or obj.empty:
        return "vacio"

    lineas = [f"filas={len(obj)}"]

    cols = list(obj.columns)
    lineas.append(f"    columnas ({len(cols)}): "
                  f"{cols if len(cols) <= 12 else cols[:12] + ['...']}")

    try:
        lineas.append(f"    rango indice: {obj.index.min()} -> {obj.index.max()}")
        if len(obj.index) > 1 and isinstance(obj.index, pd.DatetimeIndex):
            paso = pd.Series(obj.index).diff().dropna().mode()
            if len(paso):
                lineas.append(f"    granularidad modal: {paso.iloc[0]}")
    except Exception:
        pass

    numericas = [c for c in cols if pd.api.types.is_numeric_dtype(obj[c])]
    for col in numericas[:6]:
        s = obj[col]
        lineas.append(f"    {col}: nulos={s.isna().sum()}  "
                      f"min={s.min():.2f}  max={s.max():.2f}  media={s.mean():.2f}")
    if len(numericas) > 6:
        lineas.append(f"    ... y {len(numericas) - 6} columnas numericas mas")

    lineas.append("    muestra:")
    with pd.option_context("display.max_columns", 8, "display.width", 200):
        for linea in obj.head(3).to_string().splitlines():
            lineas.append(f"      {linea[:190]}")
    return "\n".join(lineas)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dia", type=date.fromisoformat,
                   default=date.today() - timedelta(days=2),
                   help="Dia de referencia (por defecto, anteayer)")
    p.add_argument("--solo", type=str, default=None,
                   help="Filtra pruebas cuya clave contenga este texto")
    p.add_argument("--verbose-errores", action="store_true")
    args = p.parse_args()

    client = EntsoePandasClient(api_key=leer_token())

    print("=" * 78)
    print(f"TEST_415 - Endpoints extra ENTSO-E {PAIS} - referencia {args.dia}")
    print("=" * 78)

    ok, vacio, error = [], [], []

    for clave, titulo, dias, fn, coment in construir_pruebas():
        if args.solo and args.solo not in clave:
            continue

        fin = pd.Timestamp(args.dia + timedelta(days=1), tz=TZ)
        ini = fin - pd.Timedelta(days=dias)

        print(f"\n--- [{clave}] {titulo}")
        print(f"    {coment}")
        print(f"    ventana: {ini.date()} -> {fin.date()}")
        try:
            texto = describir(fn(client, ini, fin))
            print(f"    {texto}")
            (vacio if texto in ("vacio", "None") else ok).append(clave)
        except Exception as e:
            error.append((clave, type(e).__name__, str(e)[:110]))
            if args.verbose_errores:
                traceback.print_exc()
            else:
                print(f"    ERROR {type(e).__name__}: {str(e)[:110]}")

    print("\n" + "=" * 78)
    print("RESUMEN")
    print("=" * 78)
    print(f"\n  CON DATOS ({len(ok)}):")
    for d in ok:
        print(f"    + {d}")
    print(f"\n  VACIAS ({len(vacio)}):")
    for d in vacio:
        print(f"    - {d}")
    print(f"\n  CON ERROR ({len(error)}):")
    for d, tipo, msg in error:
        print(f"    x {d}  [{tipo}] {msg}")
    print("\n  Este script no ha escrito nada en base de datos.")
    print("=" * 78)


if __name__ == "__main__":
    main()
