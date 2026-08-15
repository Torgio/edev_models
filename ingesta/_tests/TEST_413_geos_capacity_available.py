"""
TEST_413 — Descubrimiento de geos e indicador 479 (turbina de gas, potencia disponible)
=======================================================================================

Objetivo
--------
`esios_capacity_available.gas_turbine_mw` está a NULL en el histórico. La hipótesis
es que ESIOS no publica agregado peninsular (geo_id=8741) para este indicador y sí
lo hace a nivel de comunidad autónoma.

Este script NO escribe en base de datos. Solo consulta la API y responde a tres
preguntas, en este orden:

  PASO 1 — ¿Qué geos publican dato para el indicador? (sin hardcodear ningún id)
  PASO 2 — ¿Cuánto vale la suma de CCAA peninsulares, día a día?
  PASO 3 — En el tramo donde SÍ hay dato peninsular, ¿coincide la suma de CCAA
           con el valor de geo_id=8741?

El PASO 3 es el que decide si se puede reconstruir el histórico. Si no cuadra,
rellenar hacia atrás sería inventar datos.

Uso
---
    python TEST_413_geos_capacity_available.py
    python TEST_413_geos_capacity_available.py --indicador 472
    python TEST_413_geos_capacity_available.py --desde 2025-07-14 --hasta 2025-08-14
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.append(str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

API_BASE = "https://api.esios.ree.es/indicators"

# El script vive en ingesta/_tests/ ; credentials.json esta en ingesta/
CREDENTIALS_CANDIDATAS = [
    Path(__file__).parent.parent / "credentials.json",
    Path.home() / "scripts" / "ingesta" / "credentials.json",
]

GEO_PENINSULA = 8741

# Geos agregados: no se suman, se usan como referencia de contraste.
GEOS_AGREGADOS = {3, 8741}

# Sistemas no peninsulares. Se detectan por nombre para no depender de ids
# que ESIOS podría cambiar. Se compara en minúsculas y sin tildes básicas.
PATRONES_NO_PENINSULARES = (
    "canaria", "balear", "ceuta", "melilla",
    "tenerife", "gran canaria", "lanzarote", "fuerteventura",
    "la palma", "la gomera", "el hierro",
    "mallorca", "menorca", "ibiza", "formentera",
)

TOLERANCIA_MW = 0.5  # diferencia aceptable entre suma CCAA y peninsular


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def leer_token() -> str:
    """Token ESIOS: variable de entorno o credentials.json."""
    token = os.environ.get("ESIOS_TOKEN")
    if token:
        return token

    claves = ("esios_token", "token_esios", "api_key_esios",
              "esios_api_key", "token", "api_key")

    for ruta in CREDENTIALS_CANDIDATAS:
        if not ruta.exists():
            continue
        cred = json.loads(ruta.read_text(encoding="utf-8"))
        for c in claves:
            if c in cred:
                print(f"  Token leido de {ruta} (clave '{c}')")
                return cred[c]
        raise SystemExit(
            f"{ruta} existe pero no contiene ninguna clave de token conocida.\n"
            f"  Claves presentes: {sorted(cred)}\n"
            f"  Anade la correcta a la tupla 'claves' en leer_token()."
        )

    raise SystemExit(
        "No se encontro credentials.json en:\n  " +
        "\n  ".join(str(r) for r in CREDENTIALS_CANDIDATAS) +
        "\n  Alternativa: $env:ESIOS_TOKEN = 'tu_token'"
    )


def cabeceras(token: str) -> dict:
    return {
        "Accept": "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
        "x-api-key": token,
    }


def es_no_peninsular(nombre: str) -> bool:
    n = (nombre or "").lower()
    return any(p in n for p in PATRONES_NO_PENINSULARES)


def descargar(indicador: int, desde: date, hasta: date, token: str,
              geo_ids: list[int] | None = None) -> pd.DataFrame:
    """Descarga el indicador en granularidad diaria.

    OJO: time_agg=average es obligatorio. La potencia disponible es MW; con la
    agregación por defecto (SUM) el valor se inflaría por el número de muestras
    del día. Es el mismo fallo que provocó las recalculaciones anteriores.
    """
    params = {
        "start_date": f"{desde}T00:00",
        "end_date": f"{hasta}T23:55",
        "time_trunc": "day",
        "time_agg": "average",
    }
    if geo_ids:
        params["geo_ids[]"] = geo_ids

    r = requests.get(f"{API_BASE}/{indicador}", params=params,
                     headers=cabeceras(token), timeout=60)
    r.raise_for_status()
    valores = r.json()["indicator"]["values"]

    if not valores:
        return pd.DataFrame(columns=["date", "geo_id", "geo_name", "value"])

    df = pd.DataFrame(valores)
    df["date"] = pd.to_datetime(df["datetime"]).dt.tz_convert(
        "Europe/Madrid").dt.date
    cols = [c for c in ("date", "geo_id", "geo_name", "value") if c in df.columns]
    return df[cols]


# ---------------------------------------------------------------------------
# Pasos
# ---------------------------------------------------------------------------

def paso_1_descubrir_geos(indicador: int, fecha: date, token: str) -> pd.DataFrame:
    print("=" * 78)
    print(f"PASO 1 — Geos que publican dato — indicador {indicador} — {fecha}")
    print("=" * 78)

    df = descargar(indicador, fecha, fecha, token)  # sin geo_ids -> todos
    if df.empty:
        print("  Sin datos para esa fecha en ningún geo.")
        print("  Prueba otra fecha antes de concluir que el indicador está vacío.")
        return df

    resumen = (df.groupby(["geo_id", "geo_name"], as_index=False)["value"]
                 .mean()
                 .sort_values("value", ascending=False))
    resumen["tipo"] = resumen.apply(
        lambda r: "AGREGADO" if r.geo_id in GEOS_AGREGADOS
        else ("NO PENINSULAR" if es_no_peninsular(r.geo_name) else "peninsular"),
        axis=1)

    for _, r in resumen.iterrows():
        print(f"  geo_id={r.geo_id:<6} {r.geo_name:<28} "
              f"{r.value:>10.2f} MW   [{r.tipo}]")

    n_pen = (resumen.tipo == "peninsular").sum()
    print(f"\n  {len(resumen)} geos con dato · {n_pen} peninsulares no agregados")
    if GEO_PENINSULA not in set(resumen.geo_id):
        print(f"  >> geo_id={GEO_PENINSULA} NO publica. Hipótesis confirmada "
              f"para esta fecha.")
    return resumen


def paso_2_serie_ccaa(indicador: int, geos: list[int], desde: date, hasta: date,
                      token: str) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print(f"PASO 2 — Serie diaria por CCAA peninsular — {desde} a {hasta}")
    print("=" * 78)

    if not geos:
        print("  No hay CCAA peninsulares que sumar.")
        return pd.DataFrame()

    df = descargar(indicador, desde, hasta, token, geo_ids=geos)
    if df.empty:
        print("  Sin datos en el rango.")
        return df

    pivot = df.pivot_table(index="date", columns="geo_name",
                           values="value", aggfunc="mean")
    pivot["SUMA_CCAA"] = pivot.sum(axis=1)
    print(pivot.round(2).to_string())
    return pivot


def paso_3_validar(indicador: int, pivot: pd.DataFrame, desde: date, hasta: date,
                   token: str) -> None:
    print("\n" + "=" * 78)
    print("PASO 3 — Contraste suma CCAA vs geo_id=8741 (peninsular)")
    print("=" * 78)

    if pivot.empty:
        print("  Nada que contrastar.")
        return

    pen = descargar(indicador, desde, hasta, token, geo_ids=[GEO_PENINSULA])
    if pen.empty:
        print(f"  geo_id={GEO_PENINSULA} no devuelve nada en este rango.")
        print("  No se puede validar aquí. Repite el test en un rango donde")
        print("  sí exista dato peninsular (según el log, desde 2025-07-14).")
        return

    comp = (pen.set_index("date")["value"].rename("PENINSULAR")
              .to_frame()
              .join(pivot["SUMA_CCAA"], how="outer"))
    comp["dif"] = (comp["SUMA_CCAA"] - comp["PENINSULAR"]).round(2)
    print(comp.round(2).to_string())

    dif_max = comp["dif"].abs().max()
    print(f"\n  Diferencia máxima absoluta: {dif_max:.2f} MW "
          f"(tolerancia {TOLERANCIA_MW})")
    if pd.isna(dif_max):
        print("  >> No hay días con ambos valores. Sin conclusión.")
    elif dif_max <= TOLERANCIA_MW:
        print("  >> CUADRA. La reconstrucción por CCAA es válida y se puede")
        print("     aplicar al histórico 2020 - 2025.")
    else:
        print("  >> NO CUADRA. Hay grupos no asignados a comunidad o un criterio")
        print("     de agregación distinto. NO rellenar el histórico así.")


# ---------------------------------------------------------------------------

def main() -> None:
    hoy = date.today()
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--indicador", type=int, default=479)
    p.add_argument("--fecha-geos", type=date.fromisoformat, default=hoy,
                   help="Fecha para el descubrimiento de geos (PASO 1)")
    p.add_argument("--desde", type=date.fromisoformat,
                   default=hoy - timedelta(days=30))
    p.add_argument("--hasta", type=date.fromisoformat, default=hoy)
    args = p.parse_args()

    token = leer_token()

    resumen = paso_1_descubrir_geos(args.indicador, args.fecha_geos, token)
    if resumen.empty:
        return

    geos_ccaa = resumen.loc[resumen.tipo == "peninsular", "geo_id"].tolist()
    pivot = paso_2_serie_ccaa(args.indicador, geos_ccaa,
                              args.desde, args.hasta, token)
    paso_3_validar(args.indicador, pivot, args.desde, args.hasta, token)

    print("\n" + "=" * 78)
    print("FIN — este script no ha escrito nada en base de datos.")
    print("=" * 78)


if __name__ == "__main__":
    main()