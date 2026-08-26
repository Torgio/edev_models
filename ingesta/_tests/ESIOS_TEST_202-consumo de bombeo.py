"""
TEST — Indicador 10249 "Prevision de la demanda residual"
Objetivo: determinar la granularidad nativa y que agregacion horaria es correcta.

Compara 4 peticiones para el mismo dia:
  A) sin time_trunc          -> muestra la granularidad nativa
  B) time_trunc=hour         -> lo que hace el pipeline AHORA (sospecha de x4)
  C) time_trunc=hour&time_agg=average
  D) time_trunc=hour&time_agg=sum

Contrasta con el visor oficial:
https://www.esios.ree.es/es/analisis/10249?vis=1&groupby=hour
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ_SPAIN = ZoneInfo("Europe/Madrid")
BASE_URL = "https://api.esios.ree.es/indicators"

IND_ID = 10249
TARGET = date(2026, 7, 28)

# credentials.json esta en ingesta/, este script en ingesta/_tests/
CREDS_PATH = Path(__file__).parent.parent / "credentials.json"


def get_headers() -> dict:
    creds = json.load(open(CREDS_PATH))
    return {
        "Host": creds["Host"],
        "x-api-key": creds["x-api-key"],
        "Accept": "application/json",
    }


def fetch(ind_id: int, target: date, headers: dict, extra: str = "") -> list:
    """Pide el dia target con margen +-1 dia y devuelve la lista cruda de values."""
    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={target}T00:00:00"
           f"&end_date={target}T23:59:59"
           f"{extra}")
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json().get("indicator", {}).get("values", [])


def parse(values: list, target: date) -> dict:
    """Filtra por dia target en hora española. Devuelve {datetime_spain: valor}."""
    out = {}
    for v in values:
        dt_str = v.get("datetime_utc") or v.get("datetime")
        val = v.get("value")
        if not dt_str or val is None:
            continue
        dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_spain = dt_utc.astimezone(TZ_SPAIN)
        if dt_spain.date() == target:
            out[dt_spain] = float(val)
    return out


def main():
    headers = get_headers()

    print("=" * 78)
    print(f"TEST indicador {IND_ID} — dia {TARGET}")
    print("=" * 78)

    # ── A) Granularidad nativa (sin truncar) ──────────────────────────────────
    raw = fetch(IND_ID, TARGET, headers)
    nat = parse(raw, TARGET)
    print(f"\nA) SIN time_trunc  -> {len(nat)} valores en el dia")
    if len(nat) == 24:
        print("   => granularidad NATIVA HORARIA (no necesita agregacion)")
    elif len(nat) == 96:
        print("   => granularidad NATIVA CUARTO-HORARIA (necesita agregacion)")
    elif len(nat) == 288:
        print("   => granularidad NATIVA 5-MINUTAL (necesita agregacion)")
    else:
        print(f"   => granularidad no estandar: {len(nat)} valores")

    if nat:
        primeras = sorted(nat.items())[:8]
        print("   primeros valores:")
        for ts, v in primeras:
            print(f"     {ts.strftime('%H:%M')}  {v:>12,.2f}")

    # Referencia: media y suma de la primera hora nativa
    if nat and len(nat) > 24:
        h0 = [v for ts, v in sorted(nat.items()) if ts.hour == 0]
        print(f"\n   Hora 00:00 -> {len(h0)} muestras nativas")
        print(f"     media = {sum(h0)/len(h0):>12,.2f}")
        print(f"     suma  = {sum(h0):>12,.2f}")

    # ── B, C, D) Comparativa de agregaciones ──────────────────────────────────
    variantes = {
        "B) trunc=hour (pipeline actual)": "&time_trunc=hour",
        "C) trunc=hour + agg=average":     "&time_trunc=hour&time_agg=average",
        "D) trunc=hour + agg=sum":         "&time_trunc=hour&time_agg=sum",
    }

    res = {}
    for nombre, extra in variantes.items():
        try:
            res[nombre] = parse(fetch(IND_ID, TARGET, headers, extra), TARGET)
        except Exception as e:
            print(f"\n{nombre}: ERROR {e}")
            res[nombre] = {}

    print("\n" + "=" * 78)
    print("COMPARATIVA POR HORA")
    print("=" * 78)
    cab = f"{'hora':>6}"
    for n in variantes:
        cab += f" {n.split(')')[0]+')':>16}"
    print(cab)

    horas = sorted({ts for d in res.values() for ts in d})
    for ts in horas[:24]:
        fila = f"{ts.strftime('%H:%M'):>6}"
        for n in variantes:
            v = res[n].get(ts)
            fila += f" {v:>16,.2f}" if v is not None else f" {'-':>16}"
        print(fila)

    # ── Ratios entre variantes ────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("RATIOS (B/C) — si sale 4.0, el pipeline esta sumando cuartos de hora")
    print("=" * 78)
    b = res.get("B) trunc=hour (pipeline actual)", {})
    c = res.get("C) trunc=hour + agg=average", {})
    ratios = []
    for ts in horas[:24]:
        if ts in b and ts in c and c[ts]:
            r = b[ts] / c[ts]
            ratios.append(r)
            print(f"  {ts.strftime('%H:%M')}  B={b[ts]:>12,.2f}  C={c[ts]:>12,.2f}  ratio={r:.3f}")
    if ratios:
        print(f"\n  ratio medio: {sum(ratios)/len(ratios):.3f}")

    # ── Veredicto ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print("VEREDICTO")
    print("=" * 78)
    print(f"  Valores nativos en el dia: {len(nat)}")
    if ratios:
        rm = sum(ratios) / len(ratios)
        if abs(rm - 1.0) < 0.01:
            print("  time_trunc=hour ya devuelve la MEDIA -> el pipeline esta correcto")
        elif abs(rm - 4.0) < 0.05:
            print("  time_trunc=hour SUMA los 4 cuartos -> BUG x4 CONFIRMADO")
            print("  FIX: anadir &time_agg=average a la URL de fetch_indicator_for_day()")
        else:
            print(f"  ratio inesperado ({rm:.3f}) -> revisar manualmente")
    print()
    print("  Contrastar la columna C contra el visor oficial:")
    print(f"  https://www.esios.ree.es/es/analisis/{IND_ID}?vis=1&groupby=hour"
          f"&start_date={TARGET.strftime('%d-%m-%Y')}T00%3A00"
          f"&end_date={TARGET.strftime('%d-%m-%Y')}T23%3A55")
    print("=" * 78)


if __name__ == "__main__":
    main()