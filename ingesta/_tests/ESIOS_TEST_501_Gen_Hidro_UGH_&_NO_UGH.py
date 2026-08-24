"""
TFM Energia UCM — TEST 504: verificar la agregacion del indicador 1295 (FV)
===========================================================================
LA ANOMALIA
En el TEST 503, el indicador 1295 (Generacion T.Real Solar fotovoltaica) dio
para el 11-ago-2026, con geo_ids[]=8741 y time_agg=average:

    media 13.898 MW    maximo 32.346 MW

El maximo es IMPOSIBLE: la potencia fotovoltaica instalada en la Peninsula
ronda los 30 GW y no puede estar toda produciendo por encima de su nominal.
Ademas, el mismo indicador dio 12.462 MW de media en junio de 2026, un valor
plausible.

DOS HIPOTESIS
  (a) BUG DE AGREGACION. Ya ocurrio dos veces en este proyecto:
      - esios_marketdata: faltaba time_agg=average en indicadores nativos de
        5 minutos y los valores salian inflados x11-12 (suma de 12 muestras en
        vez de promedio). Se corrigieron 854.092 celdas.
      - esios_forecast_da: time_trunc=hour sin time_agg, y ESIOS agrega por
        SUMA por defecto, no por media. Ratio x4 exacto en indicadores
        cuarto-horarios. Se corrigieron 311.910 celdas.
      Si el 1295 hubiera cambiado de granularidad nativa (de 5 a 15 min, por
      ejemplo), el promedio podria comportarse de forma distinta.

  (b) EL DATO ES REAL pero mide otra cosa desde alguna fecha. Hay precedente:
      el indicador 1775 (demanda prevista) incorporo la estimacion de consumo
      alimentado por autoconsumo desde el 11/12/2025, creando un salto de nivel
      de hasta 5 GW. Si al 1295 le hubiera pasado algo parecido, el escalon
      seria genuino y habria que documentarlo, no corregirlo.

METODO — el mismo que caza los dos bugs anteriores:
  1. Pedir el indicador SIN time_agg para ver la granularidad nativa real y
     los valores crudos de una hora concreta.
  2. Calcular la media a mano sobre esas muestras.
  3. Compararla con lo que devuelve time_agg=average y con time_agg=sum.
  4. Si media_manual == average -> la agregacion es correcta y el dato es real.
     Si average == suma de muestras -> ESIOS esta sumando, no promediando.
  5. Revisar la evolucion mensual para localizar cuando aparece el salto.

USO
    python ESIOS_TEST_504_fv_agregacion.py
    python ESIOS_TEST_504_fv_agregacion.py --fecha 2026-08-11 --hora 14
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

BASE_URL   = "https://api.esios.ree.es/indicators"
TIMEOUT    = 60
CREDS_PATH = Path(__file__).parent.parent / "credentials.json"

IND_FV        = 1295
GEO_PENINSULA = 8741

# Potencia FV instalada aproximada en la Peninsula, para juzgar plausibilidad
POTENCIA_FV_INSTALADA_MW = 30000


def get_headers() -> dict:
    creds = json.load(open(CREDS_PATH))
    return {"Host": creds["Host"], "x-api-key": creds["x-api-key"],
            "Accept": "application/json"}


def pedir(fecha, headers, agg=None, ind_id=IND_FV):
    url = (f"{BASE_URL}/{ind_id}"
           f"?start_date={fecha}T00:00:00&end_date={fecha}T23:59:59"
           f"&geo_ids[]={GEO_PENINSULA}")
    if agg:
        url += f"&time_trunc=hour&time_agg={agg}"
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("indicator", {}).get("values", [])


def a_dict(vals):
    out = {}
    for v in vals:
        if v.get("value") is None:
            continue
        dt = datetime.fromisoformat(v["datetime"].replace("Z", "+00:00"))
        out[dt] = float(v["value"])
    return out


# ── 1. Granularidad nativa ────────────────────────────────────────────────────

def granularidad(headers, fecha, hora):
    print("=" * 88)
    print("1. GRANULARIDAD NATIVA Y VALORES CRUDOS")
    print("=" * 88)

    crudos = a_dict(pedir(fecha, headers))
    if not crudos:
        print("  Sin datos crudos.")
        return {}

    n = len(crudos)
    esperado = {288: "5 min", 144: "10 min", 96: "15 min", 24: "horaria"}
    gran = esperado.get(n, f"{n} valores ({24*60/n:.0f} min aprox)")
    print(f"  {n} valores en el dia -> granularidad {gran}")

    de_la_hora = {dt: v for dt, v in crudos.items() if dt.hour == hora}
    print(f"\n  Muestras crudas de las {hora:02d}:00 ({len(de_la_hora)} valores):")
    for dt in sorted(de_la_hora):
        print(f"    {dt:%H:%M}  {de_la_hora[dt]:>10.2f} MW")

    if de_la_hora:
        vals = list(de_la_hora.values())
        media = sum(vals) / len(vals)
        suma  = sum(vals)
        print(f"\n    media manual : {media:>10.2f} MW")
        print(f"    suma manual  : {suma:>10.2f} MW")
        print(f"    maximo crudo : {max(vals):>10.2f} MW")
        return {"crudos": crudos, "hora": de_la_hora,
                "media": media, "suma": suma}
    return {"crudos": crudos, "hora": {}, "media": None, "suma": None}


# ── 2. Que devuelve la API con cada agregacion ────────────────────────────────

def comparar_agregaciones(headers, fecha, hora, manual):
    print("\n" + "=" * 88)
    print("2. QUE DEVUELVE LA API CON CADA time_agg")
    print("=" * 88)

    resultados = {}
    for agg in ("average", "sum", None):
        etiqueta = agg or "sin time_agg (default de ESIOS)"
        try:
            d = a_dict(pedir(fecha, headers, agg=agg if agg else "average"
                             if False else agg))
        except Exception as e:
            print(f"  {etiqueta:<32} ERROR {str(e)[:40]}")
            continue
        if agg is None:
            # sin agregar son los crudos; se agrupan a mano para comparar
            por_hora = defaultdict(list)
            for dt, v in d.items():
                por_hora[dt.hour].append(v)
            valor = (sum(por_hora[hora]) / len(por_hora[hora])
                     if por_hora.get(hora) else None)
            print(f"  {'crudos, media manual':<32} "
                  f"{valor:>12.2f}" if valor else f"  {etiqueta:<32} sin datos")
            resultados["crudo_media"] = valor
        else:
            valor = next((v for dt, v in d.items() if dt.hour == hora), None)
            v_s = f"{valor:>12.2f}" if valor is not None else f"{'-':>12}"
            print(f"  time_agg={etiqueta:<24} {v_s}")
            resultados[agg] = valor

    print()
    avg, sm = resultados.get("average"), resultados.get("sum")
    med, suma = manual.get("media"), manual.get("suma")

    if avg is not None and med is not None:
        if abs(avg - med) < 1:
            print("  >>> time_agg=average COINCIDE con la media manual: la")
            print("      agregacion es CORRECTA y el dato es real.")
        elif suma and abs(avg - suma) < 1:
            print("  >>> time_agg=average devuelve la SUMA, no la media.")
            print("      Es el mismo bug que hubo en esios_forecast_da.")
        else:
            ratio = avg / med if med else 0
            print(f"  >>> No coincide con nada conocido: ratio {ratio:.2f}")
            print("      Revisar si la granularidad nativa cambio de fecha.")


# ── 3. Evolucion mensual: ¿cuando aparece el salto? ───────────────────────────

def evolucion(headers):
    print("\n" + "=" * 88)
    print("3. EVOLUCION MENSUAL DEL MAXIMO — ¿cuando aparece el salto?")
    print("=" * 88)
    print(f"  Potencia FV instalada en la Peninsula: ~{POTENCIA_FV_INSTALADA_MW:,} MW.")
    print("  Un maximo horario por encima de esa cifra es imposible y señala")
    print("  un problema de agregacion o un cambio de definicion.\n")

    fechas = [f"{y}-{m:02d}-15" for y in (2023, 2024, 2025, 2026)
              for m in (1, 4, 7, 10)]
    fechas = [f for f in fechas if f <= "2026-08-13"]

    print(f"  {'fecha':<12} {'n crudos':>9} {'media':>10} {'maximo':>10}  plausible")
    for f in fechas:
        try:
            d = a_dict(pedir(f, headers, agg="average"))
        except Exception as e:
            print(f"  {f:<12} ERROR {str(e)[:40]}")
            continue
        if not d:
            print(f"  {f:<12} {'—':>9} {'—':>10} {'—':>10}")
            continue
        vals = list(d.values())
        try:
            n_crudos = len(pedir(f, headers))
        except Exception:
            n_crudos = 0
        mx = max(vals)
        ok = "si" if mx <= POTENCIA_FV_INSTALADA_MW else "NO — imposible"
        print(f"  {f:<12} {n_crudos:>9} {sum(vals)/len(vals):>10.1f} "
              f"{mx:>10.1f}  {ok}")

    print("""
  Si el numero de valores crudos cambia de una fecha a otra, la granularidad
  nativa del indicador ha cambiado y la agregacion hay que revisarla para el
  tramo afectado. Si se mantiene y aun asi el maximo se dispara, el cambio esta
  en lo que mide el indicador, no en como se agrega.""")


def main():
    p = argparse.ArgumentParser(description="Agregacion del 1295 (FV)")
    p.add_argument("--fecha", default="2026-08-11")
    p.add_argument("--hora", type=int, default=14,
                   help="hora a inspeccionar en detalle (por defecto 14, pico solar)")
    args = p.parse_args()

    headers = get_headers()
    print("\n" + "=" * 88)
    print(f"  TEST 504 — AGREGACION DEL INDICADOR {IND_FV} (SOLAR FOTOVOLTAICA)")
    print(f"  {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 88 + "\n")

    manual = granularidad(headers, args.fecha, args.hora)
    if manual.get("hora"):
        comparar_agregaciones(headers, args.fecha, args.hora, manual)
    evolucion(headers)
    print("\n" + "=" * 88)


if __name__ == "__main__":
    main()