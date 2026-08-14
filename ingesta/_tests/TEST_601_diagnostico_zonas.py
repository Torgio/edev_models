"""
TFM Energia UCM - Diagnostico de zonas de precio  (NO ESCRIBE EN LA BD)

Para que sirve
--------------
Antes de crear columnas y lanzar 6 anos de carga, comprobar:

  1. Que geo_ids expone realmente el indicador 600 de ESIOS.
  2. Que zonas de ENTSO-E devuelven datos y cuales vienen vacias
     (un codigo de zona mal escrito da "sin datos", no un error).
  3. Si ESIOS y ENTSO-E coinciden para la misma zona -> si coinciden,
     tener las dos fuentes es una columna redundante.
  4. Que valores reales tiene cada zona y como de correlacionada esta
     con Espana: una zona casi identica a otra que ya tienes no aporta.

Solo hace peticiones de lectura. No abre conexion a PostgreSQL.

Uso
---
  python diagnostico_zonas.py                    # ultimos 7 dias
  python diagnostico_zonas.py --dias 30
  python diagnostico_zonas.py --fecha 2026-08-13 # un dia concreto
"""

import sys
import json
import time
import argparse
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

TZ = "Europe/Madrid"
Q2 = Decimal("0.01")
TIMEOUT = 60

# Zonas ENTSO-E a evaluar (codigo entsoe-py -> nombre)
ZONAS_ENTSOE = {
    "ES": "Espana",
    "PT": "Portugal",
    "FR": "Francia",
    "DE_LU": "Alemania-Luxemburgo",
    "BE": "Belgica",
    "NL": "Paises Bajos",
    "AT": "Austria",
    "CH": "Suiza",
    "PL": "Polonia",
    "CZ": "Chequia",
    "SK": "Eslovaquia",
    "SI": "Eslovenia",
    "HU": "Hungria",
    "IT_NORD": "Italia Norte",
}


def redondear(v):
    if v is None or pd.isna(v):
        return None
    return Decimal(str(v)).quantize(Q2, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# 1. Que geos publica el indicador 600
# ---------------------------------------------------------------------------
def geos_de_esios(headers):
    print("=" * 70)
    print("  1. GEO_IDS DISPONIBLES EN EL INDICADOR 600 DE ESIOS")
    print("=" * 70)
    try:
        r = requests.get("https://api.esios.ree.es/indicators/600",
                         headers=headers, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code} -- revisa el token")
            return {}
        ind = r.json().get("indicator", {})
        geos = ind.get("geos", [])
        if not geos:
            # algunos indicadores solo listan geos junto con valores
            r = requests.get("https://api.esios.ree.es/indicators/600",
                             headers=headers, timeout=TIMEOUT,
                             params={"start_date": f"{date.today()}T00:00:00",
                                     "end_date": f"{date.today()}T23:59:59",
                                     "time_trunc": "hour"})
            vals = r.json().get("indicator", {}).get("values", [])
            geos = [{"geo_id": g, "geo_name": n} for g, n in
                    sorted({(v.get("geo_id"), v.get("geo_name")) for v in vals})]

        print(f"  {len(geos)} zonas:\n")
        print(f"  {'geo_id':>8}  {'nombre'}")
        out = {}
        for g in geos:
            gid = g.get("geo_id") or g.get("id")
            nom = g.get("geo_name") or g.get("name")
            print(f"  {str(gid):>8}  {nom}")
            out[nom] = gid
        return out
    except Exception as e:
        print(f"  error: {e}")
        return {}


# ---------------------------------------------------------------------------
# 2. Precio ESIOS de un geo
# ---------------------------------------------------------------------------
def precio_esios(ini, fin, geo_id, headers):
    try:
        r = requests.get("https://api.esios.ree.es/indicators/600",
                         headers=headers, timeout=TIMEOUT,
                         params={"start_date": f"{ini}T00:00:00",
                                 "end_date": f"{fin}T23:59:59",
                                 "time_trunc": "hour", "geo_ids[]": geo_id})
        if r.status_code != 200:
            return None
        vals = r.json().get("indicator", {}).get("values", [])
        if not vals:
            return None
        df = pd.DataFrame(vals)[["datetime", "value"]]
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_convert(TZ)
        # desde MTU15 time_trunc=hour SUMA los 4 cuartos
        mask = df["datetime"].dt.date >= date(2025, 10, 1)
        df.loc[mask, "value"] = df.loc[mask, "value"] / 4
        return df.set_index("datetime")["value"].map(redondear)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3. Precio ENTSO-E de una zona
# ---------------------------------------------------------------------------
def precio_entsoe(ini, fin, zona, client):
    t0 = pd.Timestamp(str(ini), tz=TZ)
    t1 = pd.Timestamp(str(fin), tz=TZ) + pd.Timedelta(days=1)
    try:
        s = client.query_day_ahead_prices(zona, start=t0, end=t1)
    except Exception as e:
        return None, type(e).__name__
    if s is None or s.empty:
        return None, "vacio"
    s = s[s.index < t1]
    if s.empty:
        return None, "vacio"
    g = s.groupby(pd.Grouper(freq="h"))
    modo = g.count().mode()
    esp = int(modo.iloc[0]) if len(modo) else 1
    val = g.count()[g.count() == esp].index
    h = g.mean().loc[val].map(redondear)
    return h, f"{len(s)} pts -> {len(h)} horas"


# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dias", type=int, default=7)
    p.add_argument("--fecha", help="YYYY-MM-DD, un solo dia")
    a = p.parse_args()

    if a.fecha:
        ini = fin = date.fromisoformat(a.fecha)
    else:
        fin = date.today()
        ini = fin - timedelta(days=a.dias - 1)

    print(f"Diagnostico de zonas -- {ini} a {fin}")
    print("NO se escribe nada en la base de datos\n")

    headers, _ = load_config()
    from entsoe import EntsoePandasClient
    creds = json.load(open(Path(__file__).parent.parent / "credentials.json"))
    client = EntsoePandasClient(api_key=creds["entsoe_token"])

    geos = geos_de_esios(headers)

    # ---- 2. ENTSO-E zona a zona ----
    print("\n" + "=" * 70)
    print("  2. ZONAS ENTSO-E: DISPONIBILIDAD Y VALORES")
    print("=" * 70)
    print(f"  {'zona':<10} {'nombre':<22} {'horas':>6} {'media':>9} {'min':>9} {'max':>9}")
    series = {}
    for z, nom in ZONAS_ENTSOE.items():
        s, info = precio_entsoe(ini, fin, z, client)
        if s is None:
            print(f"  {z:<10} {nom:<22} {'--':>6}  SIN DATOS ({info})")
        else:
            f = s.astype(float)
            print(f"  {z:<10} {nom:<22} {len(s):>6} {f.mean():>9.2f} {f.min():>9.2f} {f.max():>9.2f}")
            series[z] = f
        time.sleep(0.8)

    # ---- 3. ESIOS vs ENTSO-E para las zonas que ESIOS publica ----
    print("\n" + "=" * 70)
    print("  3. ESIOS vs ENTSO-E EN LA MISMA ZONA")
    print("  (si coinciden, tener las dos fuentes es una columna redundante)")
    print("=" * 70)
    if not geos:
        print("  no se pudieron listar los geos de ESIOS, se omite")
    else:
        equivalencias = {"Espana": "ES", "España": "ES", "Portugal": "PT",
                         "Francia": "FR", "France": "FR",
                         "Alemania": "DE_LU", "Germany": "DE_LU",
                         "Belgica": "BE", "Bélgica": "BE", "Belgium": "BE",
                         "Paises Bajos": "NL", "Países Bajos": "NL",
                         "Netherlands": "NL", "Holanda": "NL"}
        for nom, gid in geos.items():
            z = equivalencias.get(nom)
            if not z or z not in series:
                continue
            se = precio_esios(ini, fin, gid, headers)
            if se is None:
                print(f"  {nom:<20} geo {gid:<5} ESIOS sin datos")
                continue
            se = se.astype(float)
            comun = se.index.intersection(series[z].index)
            if len(comun) == 0:
                print(f"  {nom:<20} geo {gid:<5} sin horas comunes")
                continue
            d = (se.loc[comun] - series[z].loc[comun]).abs()
            print(f"  {nom:<20} geo {gid:<5} {len(comun):>5} horas comunes  "
                  f"dif media {d.mean():.4f}  max {d.max():.2f}  "
                  f"iguales {int((d < 0.005).sum())}/{len(comun)}")
            time.sleep(0.5)

    # ---- 4. Correlacion con Espana ----
    print("\n" + "=" * 70)
    print("  4. RELACION CON ESPANA")
    print("  Correlacion alta + spread bajo = zona redundante, no aporta")
    print("=" * 70)
    if "ES" not in series:
        print("  sin serie de Espana, se omite")
    else:
        es = series["ES"]
        print(f"  {'zona':<10} {'horas':>6} {'corr':>7} {'spread medio':>13} {'sd':>9} {'|dif|>1':>9}")
        filas = []
        for z, s in series.items():
            if z == "ES":
                continue
            c = es.index.intersection(s.index)
            if len(c) < 10:
                continue
            dif = s.loc[c] - es.loc[c]
            filas.append((z, len(c), es.loc[c].corr(s.loc[c]), dif.mean(),
                          dif.std(), 100 * (dif.abs() > 1).mean()))
        for z, n, corr, med, sd, pct in sorted(filas, key=lambda x: -x[2]):
            print(f"  {z:<10} {n:>6} {corr:>7.3f} {med:>13.2f} {sd:>9.2f} {pct:>8.1f}%")

        print("\n  Lectura:")
        print("   corr > 0.95 y spread sd baja -> practicamente la misma serie")
        print("   spread con sd alta           -> informacion propia, interesante")

    print(f"\nFin: {datetime.now()}")


if __name__ == "__main__":
    main()
