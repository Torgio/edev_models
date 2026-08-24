#!/usr/bin/env python3
"""
TEST_418 - Sonda del contrato EUA Dic-26 en /api/trades de Trayport.

MOTIVO
La carga historica de EUA se detuvo en Dic-25. En trayport_history.py, la
linea 268 trata el 403 como ErrorEstructural con el mensaje "token invalido
o sin permiso" y aborta la ejecucion completa. Pero Trayport devuelve 403
tambien por LIMITE DE PETICIONES, y en ese caso reintentar con espera SI
funciona. La hipotesis es que un 403 por ritmo detuvo la carga del Dic-26.

QUE HACE
  1. Una sola peticion corta, para ver el codigo de estado sin gastar cuota.
  2. Si devuelve 200, recorre el rango completo en tramos, con pausa
     configurable, y cuenta operaciones por tramo.
  3. Ante un 403, ESPERA y reintenta en vez de abortar — que es justo el
     comportamiento que hay que llevar al script de produccion.

NO ESCRIBE EN BASE DE DATOS. Solo hace GET y cuenta.

Uso:
    python TEST_418_trayport_eua_dic26.py                    # sonda + rango 2026
    python TEST_418_trayport_eua_dic26.py --solo-sonda       # 1 peticion y salir
    python TEST_418_trayport_eua_dic26.py --pausa 3          # mas conservador
    python TEST_418_trayport_eua_dic26.py --anio 2027        # probar Dic-27
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

URL = "https://analytics.trayport.com/api/trades"

# Configuracion EUA copiada de trayport_history.py (no se importa el modulo
# para que este test sea independiente y no arrastre su conexion a la BD).
EUA = {
    "commodity":    "EUA",
    "instrumentId": 10003008,
    "sequenceId":   10000400,
    "ancla_item":   830,      # Dic-26
    "ancla_anio":   2026,
    "paso":         40,       # +40 unidades por anio
}

AQUI = Path(__file__).resolve().parent
CANDIDATOS_CRED = [
    AQUI.parent / "credentials.json",          # .../ingesta/credentials.json
    AQUI / "credentials.json",
    Path.cwd() / "ingesta" / "credentials.json",
    Path.cwd() / "credentials.json",
    Path("/home/ubuntu/scripts/ingesta/credentials.json"),
]


def item_eua(anio: int) -> int:
    return EUA["ancla_item"] + (anio - EUA["ancla_anio"]) * EUA["paso"]


def cargar_headers():
    if os.environ.get("TRAYPORT_API_KEY"):
        print("Clave leida de la variable de entorno TRAYPORT_API_KEY")
        return {"x-api-key": os.environ["TRAYPORT_API_KEY"],
                "Accept": "application/json"}

    ruta = next((p for p in CANDIDATOS_CRED if p.exists()), None)
    if ruta is None:
        print("ERROR: no encuentro credentials.json. Rutas probadas:")
        for p in CANDIDATOS_CRED:
            print(f"   {p}")
        print('\nAlternativa (PowerShell):  $env:TRAYPORT_API_KEY = "tu_clave"')
        sys.exit(1)

    creds = json.load(open(ruta, encoding="utf-8"))
    key = creds.get("trayport_api_key")
    if not key:
        sys.exit(f'ERROR: falta "trayport_api_key" en {ruta}. Claves: {list(creds)}')
    print(f"Credenciales: {ruta}")
    return {"x-api-key": key, "Accept": "application/json"}


def pedir(item, ini: date, fin: date, headers, pausa_403=15, reintentos=3):
    """
    Un tramo. Devuelve (lista_trades, codigo, motivo).

    La diferencia con trayport_history.py esta aqui: el 403 NO aborta.
    Se espera y se reintenta, porque Trayport lo usa para limitar el ritmo.
    """
    params = {
        "from":           f"{ini}T00:00:00Z",
        "until":          f"{fin}T00:00:00Z",
        "contractType":   "SinglePeriod",
        "instrumentId":   EUA["instrumentId"],
        "sequenceId":     EUA["sequenceId"],
        "sequenceItemId": item,
    }
    for intento in range(reintentos + 1):
        try:
            r = requests.get(URL, params=params, headers=headers, timeout=60)
        except requests.RequestException as e:
            if intento < reintentos:
                time.sleep(2 * (intento + 1))
                continue
            return None, None, f"excepcion {type(e).__name__}"

        if r.status_code == 200:
            try:
                d = r.json()
            except Exception:
                return None, 200, "respuesta no JSON"
            return (d if isinstance(d, list) else []), 200, None

        if r.status_code == 401:
            # Esto si es estructural: la clave no vale y esperar no ayuda.
            return None, 401, "token invalido — abortar"

        if r.status_code == 403:
            if intento < reintentos:
                espera = pausa_403 * (intento + 1)
                print(f"      403 -> esperando {espera}s y reintentando "
                      f"({intento + 1}/{reintentos})")
                time.sleep(espera)
                continue
            return None, 403, "403 persistente tras reintentos"

        if r.status_code == 429:
            if intento < reintentos:
                time.sleep(pausa_403 * (intento + 1))
                continue
            return None, 429, "429 persistente"

        motivo = f"HTTP {r.status_code}"
        try:
            det = r.json().get("errors") or r.json().get("title")
            motivo += f" {str(det)[:70]}"
        except Exception:
            pass
        if r.status_code == 400:
            return None, 400, motivo
        if intento < reintentos:
            time.sleep(2 * (intento + 1))
    return None, None, "sin respuesta"


def a_fecha(dd):
    """dealDate viene en nanosegundos desde epoch (visto en trayport_history)."""
    try:
        return datetime.fromtimestamp(int(dd) / 1e9, tz=timezone.utc).date()
    except Exception:
        return None


def resumen_trades(trades):
    """Fechas cubiertas y rango de precios, sin asumir nombres de campo."""
    fechas, precios = [], []
    for t in trades:
        if not isinstance(t, dict):
            continue
        for k in ("dealDate", "dealdate", "date", "timestamp"):
            if t.get(k) is not None:
                f = a_fecha(t[k])
                if f:
                    fechas.append(f)
                break
        for k in ("price", "Price", "dealPrice", "value"):
            if t.get(k) is not None:
                try:
                    precios.append(float(t[k]))
                except (TypeError, ValueError):
                    pass
                break
    return fechas, precios


def trocear(ini: date, fin: date, dias=30):
    a = ini
    while a < fin:
        b = min(a + timedelta(days=dias), fin)
        yield a, b
        a = b


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--anio", type=int, default=2026,
                   help="anio del contrato de diciembre (default 2026)")
    p.add_argument("--desde", default=None, help="YYYY-MM-DD")
    p.add_argument("--hasta", default=None, help="YYYY-MM-DD")
    p.add_argument("--pausa", type=float, default=1.5,
                   help="segundos entre tramos (default 1.5; el script de "
                        "produccion usa 0.4, probablemente demasiado rapido)")
    p.add_argument("--solo-sonda", action="store_true",
                   help="una sola peticion y salir")
    args = p.parse_args()

    item = item_eua(args.anio)
    desde = date.fromisoformat(args.desde) if args.desde else date(args.anio, 1, 1)
    hasta = (date.fromisoformat(args.hasta) if args.hasta
             else min(date.today() - timedelta(days=1), date(args.anio, 12, 31)))

    headers = cargar_headers()
    print(f"\nTEST_418 — EUA Dic-{str(args.anio)[2:]}  item={item}")
    print(f"instrumentId={EUA['instrumentId']}  sequenceId={EUA['sequenceId']}")
    print(f"rango: {desde} .. {hasta}   pausa entre tramos: {args.pausa}s")
    print("NO escribe en base de datos\n")

    # ── PASO 1: sonda corta ───────────────────────────────────────────────
    s_ini = max(desde, hasta - timedelta(days=14))
    print(f"PASO 1 — sonda de 14 dias ({s_ini} .. {hasta})")
    trades, codigo, motivo = pedir(item, s_ini, hasta, headers, args.pausa)

    if trades is None:
        print(f"  FALLO: {motivo}  (codigo {codigo})")
        if codigo == 401:
            print("  -> la clave no es valida. Revisar trayport_api_key.")
        elif codigo == 403:
            print("  -> 403 incluso reintentando. Puede ser cuota agotada hoy,")
            print("     o que la suscripcion no cubra este contrato.")
        elif codigo == 400:
            print("  -> peticion mal formada: revisar item_id o el rango.")
        sys.exit(1)

    print(f"  OK — {len(trades)} operaciones")
    if trades:
        f, pr = resumen_trades(trades)
        if f:
            print(f"  fechas: {min(f)} .. {max(f)}")
        if pr:
            print(f"  precios: {min(pr):.2f} .. {max(pr):.2f} EUR/t")
        print(f"  campos disponibles: {sorted(trades[0].keys())}")
    else:
        print("  Respuesta valida pero VACIA: el contrato no cotizo en ese rango")
        print("  o el item_id no corresponde. Prueba --anio 2025 como control.")

    if args.solo_sonda:
        return

    # ── PASO 2: rango completo ────────────────────────────────────────────
    print(f"\nPASO 2 — rango completo en tramos de 30 dias")
    print(f"{'tramo':<26} {'ops':>7}  estado")
    print("-" * 52)
    total, fechas_all, precios_all = 0, [], []
    fallos = Counter()
    for a, b in trocear(desde, hasta):
        tr, cod, mot = pedir(item, a, b, headers, args.pausa)
        if tr is None:
            print(f"{str(a)} .. {str(b):<12} {'-':>7}  FALLO {mot}")
            fallos[cod] += 1
            if cod in (401,):
                break
        else:
            total += len(tr)
            f, pr = resumen_trades(tr)
            fechas_all += f
            precios_all += pr
            print(f"{str(a)} .. {str(b):<12} {len(tr):>7}  ok")
        time.sleep(args.pausa)

    print("-" * 52)
    print(f"TOTAL operaciones: {total}")
    if fechas_all:
        dias = sorted(set(fechas_all))
        print(f"dias habiles con operaciones: {len(dias)}  "
              f"({min(dias)} .. {max(dias)})")
    if precios_all:
        print(f"precios: {min(precios_all):.2f} .. {max(precios_all):.2f} EUR/t")
    if fallos:
        print(f"tramos fallidos: {dict(fallos)}")
    else:
        print("sin fallos — el 403 no vuelve a aparecer con esta pausa")

    if total:
        print(f"\nCONCLUSION: el contrato Dic-{str(args.anio)[2:]} SI esta "
              f"disponible.\nMerece parchear el tratamiento del 403 en "
              f"trayport_history.py y recargar.")


if __name__ == "__main__":
    main()
