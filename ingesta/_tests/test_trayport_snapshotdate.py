"""
TFM Energia UCM - Trayport: ¿respeta snapshotDate?   (NO ESCRIBE EN LA BD)

Por que este test
-----------------
El docstring de trayport_daily_pipeline.py afirma, tras 12 tests, que
snapshotDate se ignora y siempre devuelve el ultimo valor conocido. Pero los
datos ya capturados apuntan a lo contrario:

    capturado el 12-ago pidiendo el 11-ago -> TTF Dic-26 = 59.475, deal 11-ago 17:50
    capturado el 14-ago pidiendo el 13-ago -> TTF Dic-26 = 60.400, deal 13-ago 17:32

Si la fecha se ignorase, la peticion del 14 (viernes, con el mercado ya
cerrado) habria devuelto el cierre del 14, no el del 13.

EL TEST QUE LO DECIDE
Pedir hoy el 11-ago, que ya esta guardado:
    devuelve 59.475 con deal del 11-ago  -> snapshotDate FUNCIONA
                                            el historico ES recuperable
    devuelve 60.400 (o el ultimo actual) -> se ignora, dia 12 perdido

Se piden 11, 12 y 13 y se comparan. Solo lectura: no toca PostgreSQL.

Uso
---
    python test_trayport_snapshotdate.py
    python test_trayport_snapshotdate.py --dias 2026-08-11,2026-08-12,2026-08-13
"""

import sys
import json
import time
import argparse
from datetime import date, datetime, timezone
from pathlib import Path

import requests

SNAPSHOT_URL = "https://analytics.trayport.com/api/snapshots"
TIMEOUT = 30
PAUSA = 0.5

# Mismos contratos que el pipeline
TTF = {"commodity": "TTF", "instrumentId": 10002806, "sequenceId": 10000305,
       "items": {272: "Ago-26", 273: "Sep-26", 274: "Oct-26",
                 275: "Nov-26", 276: "Dic-26"}}
EUA = {"commodity": "EUA", "instrumentId": 10003008, "sequenceId": 10000400,
       "items": {830: "Dic-26", 870: "Dic-27"}}

# Lo que ya hay en la tabla, para comparar
CONOCIDO = {
    date(2026, 8, 11): {"TTF Dic-26": 59.475, "TTF Sep-26": 59.855, "EUA Dic-26": 82.44},
    date(2026, 8, 13): {"TTF Dic-26": 60.400, "TTF Sep-26": 60.700, "EUA Dic-26": 82.70},
}


def credenciales():
    """Busca credentials.json subiendo desde la carpeta del script."""
    aqui = Path(__file__).resolve().parent
    for c in (aqui / "credentials.json",
              aqui.parent / "credentials.json",
              aqui.parent.parent / "credentials.json"):
        if c.is_file():
            key = json.load(open(c, encoding="utf-8")).get("trayport_api_key")
            if not key:
                sys.exit(f'falta "trayport_api_key" en {c}')
            return {"x-api-key": key, "Accept": "application/json"}, c
    sys.exit("no se encuentra credentials.json")


def pedir(headers, cfg, f: date):
    """Devuelve {periodo: (precio, deal_date)} para esa fecha."""
    params = {
        "contractType": "SinglePeriod",
        "instrumentId": cfg["instrumentId"],
        "sequenceId": cfg["sequenceId"],
        "sequenceItemId": list(cfg["items"]),
        "snapshotDate": f"{f}T23:59:59Z",
    }
    try:
        r = requests.get(SNAPSHOT_URL, params=params, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        print(f"    excepcion: {str(e)[:70]}")
        return {}
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}")
        return {}
    try:
        data = r.json()
    except Exception:
        print("    respuesta no JSON")
        return {}

    snaps = (data.get("snapshots", []) if isinstance(data, dict)
             else (data if isinstance(data, list) else [data]))
    out = {}
    for sn in snaps:
        if not isinstance(sn, dict):
            continue
        item = sn.get("sequenceItemId") or sn.get("sequenceItem") or sn.get("itemId")
        periodo = cfg["items"].get(item, str(item))
        precio = deal = None
        lt = sn.get("lastTrade")
        if isinstance(lt, dict):
            if lt.get("price") is not None:
                precio = float(lt["price"])
            dd = lt.get("dealDate")
            if isinstance(dd, (int, float)):
                seg = dd / 1e9 if dd > 1e15 else (dd / 1e3 if dd > 1e12 else dd)
                try:
                    deal = datetime.fromtimestamp(seg, timezone.utc)
                except Exception:
                    pass
        out[f"{cfg['commodity']} {periodo}"] = (precio, deal)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dias", default="2026-08-11,2026-08-12,2026-08-13",
                   help="fechas separadas por comas")
    a = p.parse_args()
    fechas = [date.fromisoformat(x.strip()) for x in a.dias.split(",")]

    headers, ruta = credenciales()
    print("Test: ¿respeta Trayport el parametro snapshotDate?")
    print(f"credenciales: {ruta}")
    print("NO se escribe nada en la base de datos\n")

    resultados = {}
    for f in fechas:
        print(f"--- snapshotDate = {f}T23:59:59Z ---")
        r = {}
        for cfg in (TTF, EUA):
            r.update(pedir(headers, cfg, f))
            time.sleep(PAUSA)
        resultados[f] = r
        for k in sorted(r):
            precio, deal = r[k]
            d = deal.astimezone().strftime("%Y-%m-%d %H:%M") if deal else "--"
            marca = ""
            if deal and deal.date() == f:
                marca = "  <- deal DEL DIA PEDIDO"
            print(f"    {k:<14} {str(precio):>9}   deal {d}{marca}")
        print()

    # ---- veredicto ----
    print("=" * 74)
    print("  VEREDICTO")
    print("=" * 74)

    series = sorted({k for r in resultados.values() for k in r})
    print(f"\n  {'serie':<14} " + "  ".join(f"{str(f):>12}" for f in fechas))
    distintos = 0
    for s in series:
        fila = []
        vals = []
        for f in fechas:
            precio = resultados.get(f, {}).get(s, (None, None))[0]
            fila.append(f"{str(precio):>12}")
            vals.append(precio)
        if len({v for v in vals if v is not None}) > 1:
            distintos += 1
        print(f"  {s:<14} " + "  ".join(fila))

    print(f"\n  series con valores distintos entre fechas: {distintos}/{len(series)}")

    # contraste con lo ya guardado
    print("\n  Contraste con lo que ya hay en trayport_daily:")
    aciertos = fallos = 0
    for f, esperado in CONOCIDO.items():
        if f not in resultados:
            continue
        for s, v in esperado.items():
            obt = resultados[f].get(s, (None, None))[0]
            if obt is None:
                continue
            ok = abs(obt - v) < 0.001
            aciertos += ok
            fallos += not ok
            print(f"    {f}  {s:<14} guardado {v:>9}  ahora {obt:>9}   "
                  f"{'coincide' if ok else 'NO COINCIDE'}")

    print()
    if distintos == 0:
        print("  snapshotDate SE IGNORA: las 3 fechas dan lo mismo.")
        print("  El dia 12 no es recuperable. La serie solo crece capturando")
        print("  cada dia en el momento, como ya hace el cron.")
    elif fallos == 0 and aciertos:
        print("  snapshotDate FUNCIONA: reproduce exactamente lo ya guardado.")
        print("  El dia 12 SI es recuperable, y ademas se podria reconstruir")
        print("  historico hacia atras -- mucho mas valioso que una serie que")
        print("  empieza hoy. Merece la pena probar cuanto atras llega.")
    else:
        print("  Resultado mixto: los valores varian pero no reproducen lo")
        print("  guardado. Revisar antes de dar por bueno el historico.")
    print()


if __name__ == "__main__":
    main()
