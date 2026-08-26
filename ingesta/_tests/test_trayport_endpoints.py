"""
TFM Energia UCM - Trayport: barrido de endpoints   (NO ESCRIBE EN LA BD)

Por que
-------
La pagina de producto de Trayport dice que con la Analytics API se pueden
"consultar programaticamente datos agregados como OHLC". OHLC (apertura,
maximo, minimo, cierre) ES una serie historica, y no sale de /api/snapshots,
que es el unico endpoint que se ha probado hasta ahora.

Tambien mencionan una libreria Python oficial que no esta en PyPI: se
distribuye a clientes junto con guias de ayuda propias.

Este script prueba rutas plausibles y clasifica la respuesta:

    200  existe y hay permiso            <- probar en serio
    401  existe pero falta entitlement   <- existe, hay que pedir acceso
    403  existe, prohibido               <- idem
    400  existe pero faltan parametros   <- MUY buena senal, hay que afinar
    404  no existe
    405  existe pero otro metodo (POST)  <- probar con POST

Lo mas valioso seria dar con el esquema OpenAPI/Swagger: lista todas las
rutas de golpe y ahorra adivinar.

Solo lecturas GET. No toca PostgreSQL.

Uso
---
    python test_trayport_endpoints.py
    python test_trayport_endpoints.py --con-params   # anade params de ejemplo
"""

import sys
import json
import time
import argparse
from pathlib import Path

import requests

BASE = "https://analytics.trayport.com"
TIMEOUT = 20
PAUSA = 0.25

# Esquemas de documentacion: si alguno responde, se acabo el adivinar
DOCS = [
    "/swagger/v1/swagger.json",
    "/swagger/index.html",
    "/swagger",
    "/openapi.json",
    "/api/swagger.json",
    "/api/openapi.json",
    "/api-docs",
    "/api/docs",
    "/api",
    "/api/v1",
]

# Rutas de datos. El orden va de mas a menos prometedor.
RUTAS = [
    # OHLC: lo que menciona la web, series agregadas
    "/api/ohlc",
    "/api/ohlcv",
    "/api/candles",
    "/api/bars",
    "/api/aggregates",
    "/api/aggregate",
    # series temporales
    "/api/timeseries",
    "/api/timeSeries",
    "/api/history",
    "/api/historical",
    "/api/prices",
    "/api/priceHistory",
    "/api/marketdata",
    "/api/curves",
    "/api/settlements",
    "/api/eod",
    "/api/endofday",
    # ya conocidos o descartados, como control
    "/api/snapshots",
    "/api/trades",
    "/api/orders",
    "/api/orderbook",
    # catalogo
    "/api/instruments",
    "/api/sequences",
    "/api/contracts",
    "/api/markets",
    "/api/venues",
    "/api/entitlements",
    "/api/subscriptions",
]

# Parametros de ejemplo (TTF Dic-26), por si un 400 se resuelve con ellos
PARAMS_EJEMPLO = {
    "contractType": "SinglePeriod",
    "instrumentId": 10002806,
    "sequenceId": 10000305,
    "sequenceItemId": 276,
    "from": "2026-01-01T00:00:00Z",
    "to": "2026-08-14T00:00:00Z",
    "startDate": "2026-01-01T00:00:00Z",
    "endDate": "2026-08-14T00:00:00Z",
    "interval": "Day",
}


def credenciales():
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


LECTURA = {
    200: "OK -- existe y hay permiso",
    400: "faltan/sobran parametros -- EXISTE, hay que afinar",
    401: "sin autorizacion -- existe pero sin entitlement",
    403: "prohibido -- existe pero sin entitlement",
    404: "no existe",
    405: "metodo no permitido -- existe, probar POST",
    500: "error del servidor -- existe",
}


def probar(headers, ruta, params=None):
    try:
        r = requests.get(BASE + ruta, headers=headers, params=params, timeout=TIMEOUT)
    except Exception as e:
        return None, str(e)[:50], ""
    cuerpo = ""
    try:
        j = r.json()
        cuerpo = json.dumps(j)[:110]
    except Exception:
        cuerpo = r.text[:110].replace("\n", " ")
    return r.status_code, LECTURA.get(r.status_code, f"HTTP {r.status_code}"), cuerpo


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--con-params", action="store_true",
                   help="reintenta con parametros de ejemplo las rutas que den 400")
    a = p.parse_args()

    headers, ruta_cred = credenciales()
    print("Barrido de endpoints de la Analytics API de Trayport")
    print(f"credenciales: {ruta_cred}")
    print("solo peticiones GET de lectura, no se escribe nada\n")

    interesantes = []

    print("=" * 78)
    print("  1. ESQUEMAS DE DOCUMENTACION  (si alguno responde, se acabo adivinar)")
    print("=" * 78)
    for r in DOCS:
        cod, lect, cuerpo = probar(headers, r)
        marca = "  <<<" if cod and cod != 404 else ""
        print(f"  {str(cod):>5}  {r:<30} {lect}{marca}")
        if cod and cod != 404:
            interesantes.append((r, cod, cuerpo))
        time.sleep(PAUSA)

    print("\n" + "=" * 78)
    print("  2. RUTAS DE DATOS")
    print("=" * 78)
    for r in RUTAS:
        cod, lect, cuerpo = probar(headers, r)
        marca = "  <<<" if cod and cod != 404 else ""
        print(f"  {str(cod):>5}  {r:<30} {lect}{marca}")
        if cod and cod != 404:
            interesantes.append((r, cod, cuerpo))
        time.sleep(PAUSA)

    # ---- reintento con parametros ----
    if a.con_params and interesantes:
        print("\n" + "=" * 78)
        print("  3. REINTENTO CON PARAMETROS DE EJEMPLO (TTF Dic-26, 2026)")
        print("=" * 78)
        for r, cod, _ in interesantes:
            if cod not in (400, 200):
                continue
            cod2, lect2, cuerpo2 = probar(headers, r, PARAMS_EJEMPLO)
            print(f"  {str(cod2):>5}  {r:<30} {lect2}")
            if cuerpo2:
                print(f"         {cuerpo2}")
            time.sleep(PAUSA)

    # ---- resumen ----
    print("\n" + "=" * 78)
    print("  RESUMEN")
    print("=" * 78)
    if not interesantes:
        print("\n  Ninguna ruta respondio algo distinto de 404.")
        print("  Con esto, /api/snapshots es el unico endpoint accesible y la")
        print("  conclusion de que no hay historico queda confirmada.")
    else:
        print(f"\n  {len(interesantes)} rutas responden algo distinto de 404:\n")
        for r, cod, cuerpo in interesantes:
            print(f"    {cod}  {r}")
            if cuerpo:
                print(f"         {cuerpo}")
        print("\n  Siguiente paso:")
        print("    - un 200 en swagger/openapi -> abrelo en el navegador, lista todo")
        print("    - un 400 -> el endpoint existe, faltan parametros: relanzar")
        print("      con --con-params y ajustar segun el mensaje de error")
        print("    - un 401/403 -> existe pero tu suscripcion no lo cubre;")
        print("      eso ya es argumento concreto para escribir a soporte")
    print()


if __name__ == "__main__":
    main()
