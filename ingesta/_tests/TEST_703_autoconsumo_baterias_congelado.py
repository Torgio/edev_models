"""
TEST 703 — ¿Esta congelado el indicador 2366 (autoconsumo baterias)?
=====================================================================
Pregunta que resuelve este test:

    esios_capacity_installed.autoconsume_battery_mw vale 5,00 MW en los 1.206
    dias que tiene cargados, sin moverse ni un megavatio. ¿Es la realidad, o
    el indicador esta congelado en origen?

POR QUE IMPORTA
La bateria de autoconsumo ha crecido mucho en España en ese periodo. Que la
serie no varie es dificil de creer, y a diferencia de la nuclear o el ciclo
combinado —donde una capacidad constante SI es plausible porque no se ha
construido nada nuevo— aqui la constancia contradice lo que sabemos del
sistema.

Distinguir las dos explicaciones cambia lo que hay que hacer:
  - Si ESIOS publica un valor que si varia y nosotros guardamos 5,00 fijo,
    el problema es NUESTRO: el pipeline no esta recogiendo bien la serie.
  - Si ESIOS publica 5,00 fijo desde el principio, el problema es de la
    FUENTE y la columna no sirve para nada, haya o no haya baterias.

LO QUE YA SABEMOS
El catalogo oficial (ingesta/check_tables/indicators.xlsx) da una pista: el
2366 es el unico de la familia SIN descripcion. Sus hermanos 1945 (autoconsumo
solar FV) y 10413 (autoconsumo total) traen ficha completa y nota de
publicacion ("Diariamente, incorporando la informacion mas actual
disponible"); el 2366 solo tiene el nombre. Los indicadores hibridos
(2272/2273/2275) estan igual de indocumentados. Encaja con series recientes y
poco mantenidas, pero no lo demuestra.

QUE COMPRUEBA
  1. Va directamente a la API y pide el 2366 en varias fechas repartidas por
     todo el periodo. Si ESIOS devuelve siempre lo mismo, esta congelado en
     origen y no es cosa nuestra.
  2. Compara con la familia: 1945 (solar FV) y 10413 (total). Si esas crecen
     y la de baterias no, el contraste es la prueba.
  3. Comprueba la coherencia contable: 10413 deberia ser aproximadamente
     1945 + 2366. Si no cuadra, el total incluye algo mas o alguna parte esta
     mal.

USO
    python TEST_703_autoconsumo_baterias_congelado.py
"""

import sys
import time
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

warnings.filterwarnings("ignore")

for ruta in [Path(__file__).parent.parent, Path(__file__).parent, Path("ingesta")]:
    if (ruta / "config.py").exists():
        sys.path.append(str(ruta))
        break
from config import load_config

PENINSULA = 8741

FAMILIA = {
    2366:  "autoconsumo_baterias",
    1945:  "autoconsumo_solar_fv",
    10413: "autoconsumo_total",
    2275:  "baterias_hibridadas",
}

# OJO — estos indicadores son MENSUALES. Hay que pedir el mes entero con
# time_trunc=month, no un dia suelto: preguntando por el 15 de cada mes la API
# devuelve vacio, incluso para series que si existen. Replicamos exactamente la
# consulta de esios_daily_capacity_instaled.py, que es la que funciona.
# Un mes por semestre desde 2020: suficiente para ver si la serie se mueve.
MESES = [date(a, m, 1) for a in range(2020, 2027) for m in (1, 7)
         if date(a, m, 1) <= date.today()]


def ultimo_dia(d: date) -> date:
    return date(d.year + (d.month == 12), (d.month % 12) + 1, 1) - timedelta(days=1)


def pedir(headers, ind_id, mes):
    """Valor del indicador para el mes al que pertenece 'mes', geo peninsular.
    Misma consulta que hace el pipeline de potencia instalada."""
    try:
        r = requests.get(
            f"https://api.esios.ree.es/indicators/{ind_id}",
            headers=headers,
            params={
                "start_date": mes.strftime("%Y-%m-%dT00:00:00"),
                "end_date":   ultimo_dia(mes).strftime("%Y-%m-%dT23:59:00"),
                "time_trunc": "month",
                "time_agg":   "average",
                "geo_agg":    "sum",
                "geo_trunc":  "electric_system",
            },
            timeout=30,
        )
        if r.status_code != 200:
            return None
        vals = r.json().get("indicator", {}).get("values", [])
        vals = [v for v in vals if v.get("geo_id") == PENINSULA
                and v.get("value") is not None]
        return round(float(vals[-1]["value"]), 2) if vals else None
    except Exception:
        return None


def sec(t):
    print("\n" + "=" * 74)
    print(f"  {t}")
    print("=" * 74)


def main():
    headers, _ = load_config()

    sec("1 · LA FAMILIA DEL AUTOCONSUMO, DIRECTA DESDE LA API")
    print(f"  Consultando {len(MESES)} meses x {len(FAMILIA)} indicadores. Paciencia.\n")

    filas = []
    for dia in MESES:
        fila = {"mes": dia}
        for ind_id, nombre in FAMILIA.items():
            fila[nombre] = pedir(headers, ind_id, dia)
            time.sleep(0.35)
        filas.append(fila)
        print(f"  {dia}  " + "  ".join(
            f"{n}={fila[n] if fila[n] is not None else '—':>10}" for n in FAMILIA.values()))

    df = pd.DataFrame(filas).set_index("mes")

    sec("2 · ¿SE MUEVE CADA SERIE?")
    for nombre in FAMILIA.values():
        s = df[nombre].dropna()
        if s.empty:
            print(f"  {nombre:<24} sin datos en ninguna fecha")
            continue
        distintos = s.nunique()
        print(f"  {nombre:<24} {len(s):>2} meses · {distintos:>2} valores distintos · "
              f"min {s.min():>10,.2f} · max {s.max():>10,.2f}".replace(",", "."))
        if distintos == 1:
            print(f"  {'':<24} -> CONGELADO en {s.iloc[0]}")

    sec("3 · COHERENCIA CONTABLE:  ¿total ≈ solar FV + baterias?")
    d = df.dropna(subset=["autoconsumo_total", "autoconsumo_solar_fv"])
    if d.empty:
        print("  Sin datos suficientes para comprobarlo.")
    else:
        d = d.assign(
            suma=d["autoconsumo_solar_fv"] + d["autoconsumo_baterias"].fillna(0),
            resto=lambda x: x["autoconsumo_total"] - x["suma"],
        )
        print(d[["autoconsumo_total", "autoconsumo_solar_fv",
                 "autoconsumo_baterias", "suma", "resto"]].to_string())
        print("\n  «resto» grande y creciente = el total incluye tecnologias que no")
        print("  estamos cargando. «resto» ~0 = la descomposicion esta completa.")

    sec("4 · VEREDICTO")
    bat = df["autoconsumo_baterias"].dropna()
    sol = df["autoconsumo_solar_fv"].dropna()

    if bat.empty:
        print("  El 2366 no devuelve datos por la API para ningun mes probado.")
        print("  -> La columna de la base no viene de esta consulta, o el indicador")
        print("     solo publica en otro geo. Revisar el pipeline.")
    elif bat.nunique() == 1:
        print(f"  CONGELADO EN ORIGEN. ESIOS devuelve siempre {bat.iloc[0]} MW, en todos")
        print("  los meses probados y a lo largo de seis años.")
        if not sol.empty and sol.nunique() > 1:
            print(f"\n  Contraste: el autoconsumo solar FV SI crece, de {sol.min():,.0f} a "
                  f"{sol.max():,.0f} MW.".replace(",", "."))
            print("  Es la misma familia y la misma API: el problema es del indicador 2366,")
            print("  no de nuestra ingesta.")
        print("\n  -> DESCARTAR autoconsume_battery_mw. No refleja la realidad y no")
        print("     puede aportar nada a ningun modelo. Documentarlo para que nadie")
        print("     vuelva a investigarlo.")
    else:
        print(f"  LA SERIE SI VARIA en la API: {bat.nunique()} valores distintos, de "
              f"{bat.min():,.2f} a {bat.max():,.2f} MW.".replace(",", "."))
        print("  -> Entonces el problema es NUESTRO: la base guarda 5,00 fijo pero la")
        print("     fuente publica una serie que se mueve. Revisar cómo la pide")
        print("     esios_daily_capacity_instaled.py (¿mes en curso? ¿geo correcto?).")


if __name__ == "__main__":
    main()
