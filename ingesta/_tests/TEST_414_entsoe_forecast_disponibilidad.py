"""
TEST_414 - Que variables day-ahead publica realmente ENTSO-E para ES
=====================================================================

Contexto
--------
`entsoe_forecast_da` esta vacia. Antes de rediseniar el script de carga hay que
saber que columnas del esquema se pueden rellenar de verdad y cuales se
disenaron de forma optimista.

Sospecha de partida: ENTSO-E solo publica prevision day-ahead por tecnologia
para EOLICA y SOLAR (documento A69). No hay prevision day-ahead de hidraulica
ni de nuclear. Si se confirma, esas dos columnas sobran del esquema.

Este script NO escribe en base de datos. Para cada variable objetivo lanza la
consulta correspondiente y reporta: si devuelve datos, cuantas filas, cuantos
nulos, el rango temporal y una muestra.

Uso
---
    python TEST_414_entsoe_forecast_disponibilidad.py
    python TEST_414_entsoe_forecast_disponibilidad.py --dia 2026-06-15
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

# ---------------------------------------------------------------------------

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
# Definicion de las pruebas
#
# Cada entrada:  (columna_destino, descripcion, funcion)
# La funcion recibe (client, ini, fin) y devuelve Series o DataFrame.
# ---------------------------------------------------------------------------

def construir_pruebas():
    return [
        ("load_forecast_mw",
         "Prevision de demanda day-ahead (A01)",
         lambda c, i, f: c.query_load_forecast(PAIS, start=i, end=f)),

        ("total_gen_forecast_mw",
         "Prevision de generacion total agregada (A71)",
         lambda c, i, f: c.query_generation_forecast(PAIS, start=i, end=f)),

        ("wind_forecast_mw + solar_forecast_mw",
         "Prevision eolica y solar day-ahead (A69)",
         lambda c, i, f: c.query_wind_and_solar_forecast(PAIS, start=i, end=f,
                                                         psr_type=None)),

        ("hydro_forecast_mw",
         "Prevision hidraulica -- SE ESPERA QUE FALLE (no existe A69 hidro)",
         lambda c, i, f: c.query_wind_and_solar_forecast(PAIS, start=i, end=f,
                                                         psr_type="B12")),

        ("nuclear_forecast_mw",
         "Prevision nuclear -- SE ESPERA QUE FALLE (no existe A69 nuclear)",
         lambda c, i, f: c.query_wind_and_solar_forecast(PAIS, start=i, end=f,
                                                         psr_type="B14")),

        ("net_position_forecast_mw",
         "Posicion neta day-ahead",
         lambda c, i, f: c.query_net_position(PAIS, start=i, end=f,
                                              dayahead=True)),

        ("cross_border_flow_forecast_mw (ES->FR)",
         "Intercambios programados day-ahead ES->FR",
         lambda c, i, f: c.query_scheduled_exchanges(PAIS, "FR", start=i, end=f,
                                                     dayahead=True)),

        ("cross_border_flow_forecast_mw (ES->PT)",
         "Intercambios programados day-ahead ES->PT",
         lambda c, i, f: c.query_scheduled_exchanges(PAIS, "PT", start=i, end=f,
                                                     dayahead=True)),

        ("transfer_capacity_fr_mw",
         "Capacidad de transferencia neta day-ahead ES->FR",
         lambda c, i, f: c.query_net_transfer_capacity_dayahead(PAIS, "FR",
                                                                start=i, end=f)),

        ("transfer_capacity_pt_mw",
         "Capacidad de transferencia neta day-ahead ES->PT",
         lambda c, i, f: c.query_net_transfer_capacity_dayahead(PAIS, "PT",
                                                                start=i, end=f)),

        ("(extra) precio day-ahead ES",
         "Precio del mercado diario -- para contraste con spot_price",
         lambda c, i, f: c.query_day_ahead_prices(PAIS, start=i, end=f)),

        ("(extra) precio day-ahead FR",
         "Precio frances -- variable pendiente para spread de arbitraje",
         lambda c, i, f: c.query_day_ahead_prices("FR", start=i, end=f)),
    ]


# ---------------------------------------------------------------------------

def describir(obj) -> str:
    """Resumen compacto de lo devuelto por la API."""
    if obj is None:
        return "None"

    if isinstance(obj, pd.Series):
        obj = obj.to_frame(name=obj.name or "valor")

    if not isinstance(obj, pd.DataFrame) or obj.empty:
        return "vacio"

    lineas = [f"filas={len(obj)}  columnas={list(obj.columns)}"]
    lineas.append(f"    rango: {obj.index.min()}  ->  {obj.index.max()}")

    if len(obj.index) > 1:
        paso = pd.Series(obj.index).diff().dropna().mode()
        if len(paso):
            lineas.append(f"    granularidad modal: {paso.iloc[0]}")

    for col in obj.columns:
        s = obj[col]
        nulos = s.isna().sum()
        if pd.api.types.is_numeric_dtype(s):
            lineas.append(f"    {col}: nulos={nulos}  "
                          f"min={s.min():.2f}  max={s.max():.2f}  "
                          f"media={s.mean():.2f}")
        else:
            lineas.append(f"    {col}: nulos={nulos}  (no numerica)")

    lineas.append("    muestra:")
    for linea in obj.head(3).to_string().splitlines():
        lineas.append(f"      {linea}")

    return "\n".join(lineas)


def main() -> None:
    manana = date.today() + timedelta(days=1)
    p = argparse.ArgumentParser()
    p.add_argument("--dia", type=date.fromisoformat, default=manana,
                   help="Dia objetivo del forecast (por defecto, maniana)")
    p.add_argument("--verbose-errores", action="store_true",
                   help="Traceback completo en vez de una linea")
    args = p.parse_args()

    client = EntsoePandasClient(api_key=leer_token())
    ini = pd.Timestamp(args.dia, tz=TZ)
    fin = ini + pd.Timedelta(days=1)

    print("=" * 78)
    print(f"TEST_414 - ENTSO-E day-ahead {PAIS} - dia objetivo {args.dia}")
    print(f"Ventana: {ini}  ->  {fin}")
    print("=" * 78)

    ok, vacio, error = [], [], []

    for destino, desc, fn in construir_pruebas():
        print(f"\n--- {destino}")
        print(f"    {desc}")
        try:
            res = fn(client, ini, fin)
            texto = describir(res)
            print(f"    {texto}" if texto == "vacio" else f"    {texto}")
            (vacio if texto in ("vacio", "None") else ok).append(destino)
        except Exception as e:
            error.append((destino, type(e).__name__, str(e)[:120]))
            if args.verbose_errores:
                traceback.print_exc()
            else:
                print(f"    ERROR {type(e).__name__}: {str(e)[:120]}")

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

    print("\n  Nota: 'vacio' o NoMatchingDataError en hidraulica y nuclear")
    print("  confirma que ENTSO-E no publica esa prevision day-ahead y que")
    print("  esas columnas deben salir del esquema.")
    print("\n  Este script no ha escrito nada en base de datos.")
    print("=" * 78)


if __name__ == "__main__":
    main()
