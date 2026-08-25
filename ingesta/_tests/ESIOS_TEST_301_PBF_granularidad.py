"""
TEST — Indicadores bilaterales del PBF pendientes de verificar

Ya verificados (ESIOS_TEST_303, 5 fechas de 2020 a 2026):
    421 hydro_ugh_mw          -> datos siempre
    424 nuclear_mw            -> datos siempre
    432 wind_onshore_mw       -> datos siempre
    434 solar_pv_mw           -> datos siempre (intermitente por horas nocturnas)
    437 cogen_mw              -> desde 2023
    10233 coal_mw             -> solo 2020 (cierre del carbon)
    10234 other_renew_mw      -> desde 2021
    10235 total_sales_mw      -> datos siempre
    10236 total_purchases_mw  -> datos siempre
  Descartados: 429 y 431 (cero datos en las 5 fechas, IDs en desuso; el
  bilateral de gas va por el 437), 425/426 (Anexo II RD 134/2010, derogado),
  427/428 (desglose de carbon que ya cubre el agregado 10233), 433 (eolica
  marina) y 436 (oceano/geotermica), tecnologias que no existen en España.

Este test cubre SOLO los que faltan para cerrar esios_pbf_bilateral.

Agregacion: time_agg=SUM. Los programas son MWh (P.O. 3.1, apartado 3), no MW.

Control de calidad del bilateral: total_sales_mw (10235) y total_purchases_mw
(10236) deben cuadrar entre si, porque todo contrato bilateral tiene un
vendedor y un comprador. En el test del 04/08/2026 dieron 14.256,0 y 14.255,6.
"""

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ_SPAIN = ZoneInfo("Europe/Madrid")
BASE_URL = "https://api.esios.ree.es/indicators"
PAUSE    = 0.35

FECHAS = [
    date(2020, 2, 12),
    date(2021, 7, 14),
    date(2023, 2, 15),
    date(2025, 7, 16),
    date(2026, 8, 4),
]

# Solo los pendientes de verificar
PENDIENTES = {
    # Tecnologias del lado vendedor
    422: "hydro_no_ugh_mw",
    423: "pumping_gen_mw",
    445: "pumping_cons_mw",
    430: "fuel_mw",
    435: "solar_thermal_mw",
    441: "biomass_mw",
    442: "biogas_mw",
    2142: "hybrid_mw",

    # Interconexiones
    446: "imp_fr_mw",
    450: "exp_fr_mw",
    447: "imp_pt_mw",
    451: "exp_pt_mw",
    448: "imp_ma_mw",
    452: "exp_ma_mw",
    449: "imp_ad_mw",
    453: "exp_ad_mw",

    # Lado comprador
    454: "retail_free_sales_mw",
    456: "retail_free_buy_mw",
    457: "retail_last_resort_mw",
    458: "direct_consumer_mw",
    455: "generic_sales_mw",
    459: "generic_buy_mw",

    # Tecnologias residuales a decidir (¿las cubre el agregado 10234?)
    438: "petroleo_carbon_deriv_mw",
    439: "subprod_mineria_mw",
    440: "energia_residual_mw",
    443: "residuos_domesticos_mw",
    444: "residuos_varios_mw",
}

CREDS_PATH = Path(__file__).parent.parent / "credentials.json"


def get_headers() -> dict:
    creds = json.load(open(CREDS_PATH))
    return {"Host": creds["Host"], "x-api-key": creds["x-api-key"],
            "Accept": "application/json"}


def fetch_dia(ind_id: int, target: date, headers: dict) -> dict:
    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={target}T00:00:00"
           f"&end_date={target}T23:59:59"
           f"&time_trunc=hour&time_agg=sum")
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    out = {}
    for v in r.json().get("indicator", {}).get("values", []):
        dt_str = v.get("datetime_utc") or v.get("datetime")
        val = v.get("value")
        if not dt_str or val is None:
            continue
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_spain = dt.astimezone(TZ_SPAIN)
        if dt_spain.date() == target:
            out[dt_spain] = float(val)
    return out


def main():
    headers = get_headers()

    print("=" * 96)
    print("TEST bilaterales PBF pendientes de verificar")
    print(f"Fechas     : {', '.join(str(f) for f in FECHAS)}")
    print(f"Indicadores: {len(PENDIENTES)}  |  agregacion: time_agg=sum (MWh)")
    print("=" * 96)
    print(f"  {'ID':>6}  {'2020 2021 2023 2025 2026':<26} {'media 2026-08-04':>18}  columna")
    print("-" * 96)

    resultados = {}
    for ind_id, nombre in PENDIENTES.items():
        horas, medias = [], {}
        for f in FECHAS:
            try:
                d = fetch_dia(ind_id, f, headers)
                horas.append(f"{len(d):>2}h" if d else "  -")
                if d:
                    medias[f] = sum(d.values()) / len(d)
            except Exception:
                horas.append("ERR")
            time.sleep(PAUSE)

        m_ult = medias.get(FECHAS[-1])
        m_str = f"{m_ult:>18,.1f}" if m_ult is not None else f"{'-':>18}"
        print(f"  {ind_id:>6}  {' '.join(horas):<26} {m_str}  {nombre}")
        resultados[ind_id] = {"nombre": nombre, "horas": horas, "medias": medias}

    # ── Clasificacion ──
    siempre, parcial, nunca = [], [], []
    for ind_id, r in resultados.items():
        n = sum(1 for h in r["horas"] if h.strip() not in ("-", "ERR"))
        if n == len(FECHAS):
            siempre.append((ind_id, r["nombre"]))
        elif n == 0:
            nunca.append((ind_id, r["nombre"]))
        else:
            parcial.append((ind_id, r["nombre"], n))

    print("\n" + "=" * 96)
    print("DECISION PARA esios_pbf_bilateral")
    print("=" * 96)

    print(f"\n  CARGAR como columna normal ({len(siempre)}):")
    for i, n in siempre:
        print(f"    {i:>6}  {n}")

    print(f"\n  CARGAR y marcar ESPORADICOS ({len(parcial)}):")
    for i, n, c in sorted(parcial, key=lambda x: -x[2]):
        print(f"    {i:>6}  {c}/{len(FECHAS)} fechas  {n}")

    print(f"\n  DESCARTAR — sin datos en ninguna fecha ({len(nunca)}):")
    for i, n in nunca:
        print(f"    {i:>6}  {n}")

    print("\n" + "=" * 96)
    print("NOTA sobre las tecnologias residuales (438, 439, 440, 443, 444)")
    print("=" * 96)
    print("  Si salen sin datos o con valores minimos, probablemente las cubre")
    print("  el agregado 10234 (Otras renovables) y no merecen columna propia.")
    print("=" * 96)


if __name__ == "__main__":
    main()