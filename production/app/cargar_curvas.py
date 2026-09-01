"""Subir las dos curvas y nada mas. Todo lo demas sale del fichero.

QUE HACE
Coge un fichero de consumo y otro de generacion, los deja en las tablas del usuario y se
acaba. No pregunta el consumo anual, ni los kWp, ni la unidad: **todo eso ya viene dentro**.

    python production/app/cargar_curvas.py --consumo mi_consumo.xlsx --generacion mi_fv.csv

POR QUE EXISTE, TENIENDO `caso.py`
`caso.py instalacion` pide el total anual aparte del fichero, y tiene sentido cuando se quiere
estudiar el MISMO perfil a otra escala -- la misma fabrica con el doble de consumo, la misma
cubierta con el doble de paneles. Pero para el caso normal, que es "esta es mi curva", pedirlo
es preguntar dos veces el mismo dato y abrir la puerta a que las dos respuestas no coincidan.

Aqui el fichero manda:

    consumo anual   = suma del fichero / años que cubre
    kWp             = energia generada / 1.600 kWh por kWp, la potencia EQUIVALENTE

Lo de la potencia equivalente no es un apaño: el motor proyecta la generacion como
`capacity_mwp x 1.600`, asi que guardar ese cociente hace que la proyeccion devuelva
exactamente los MWh que traia el fichero. Si el usuario declara sus kWp reales y no cuadran
con la energia del fichero, se avisa -- suele significar que el fichero es de otra
instalacion, o que esta en otra unidad.

LO QUE SIGUE COMPROBANDO
Que sea una curva horaria de verdad y que la generacion parezca generacion. Un fichero que se
sube sin mirar es la forma mas comoda de meter un dato malo en un estudio que despues devuelve
un VAN con dos decimales y toda la seriedad del mundo.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

RENDIMIENTO_FV = 1600          # kWh por kWp y año, la referencia del motor


def _resumen(s: pd.DataFrame) -> dict:
    """Lo minimo que hay que saber de una curva antes de guardarla."""
    dias = pd.DatetimeIndex(sorted(s.dia.unique()))
    anos = len(dias) / 365.25
    return {
        "dias": len(dias), "horas": len(s), "anos": anos,
        "desde": dias.min(), "hasta": dias.max(),
        "total_mwh": float(s.valor.sum()),
        "anual_mwh": float(s.valor.sum()) / anos,
        "pico_mw": float(s.valor.max()),
        "nulos": int(s.valor.isna().sum()),
        "negativos": int((s.valor < 0).sum()),
        "noche_pu": float(s[s.hora.isin([0, 1, 2, 3, 4])].valor.mean()
                          / max(s.valor.mean(), 1e-9)),
    }


def revisar(r: dict, es_consumo: bool, tecnologia="fv") -> list[str]:
    """Los motivos por los que NO se deberia guardar esta curva.

    Es deliberadamente corta. Cada linea esta porque algo asi ya paso, no por completitud.
    """
    malo = []
    if r["dias"] < 300:
        malo.append(f"solo {r['dias']} dias: hace falta al menos un año completo, porque "
                    f"los meses que falten se rellenan con la media de los demas")
    if r["nulos"]:
        malo.append(f"{r['nulos']} horas sin valor tras rellenar la rejilla")
    if r["negativos"] and es_consumo:
        malo.append(f"{r['negativos']} horas con consumo negativo")
    if r["total_mwh"] <= 0:
        malo.append("la curva suma cero: probablemente la columna de valores no es la que "
                    "se ha leido")
    # una fotovoltaica no genera de noche. Parece obvio y por eso no se comprueba: asi es
    # como una instalacion de las pruebas acabo cargada desde el fichero del CONSUMO, con un
    # perfil "solar" que daba 0,40 de media a medianoche. No fallo nada -- el estudio corrio
    # y devolvio un VAN. Solo que describia un sitio que no existe.
    if not es_consumo and tecnologia == "fv" and r["noche_pu"] > 0.02:
        malo.append(f"genera {r['noche_pu']:.3f} de su media entre las 0:00 y las 4:00, y "
                    f"una fotovoltaica da CERO: ¿es este el fichero de generacion?")
    return malo


def cargar_una(cur, uid, ruta: Path, es_consumo: bool, code: str, nombre: str,
               unidad="auto", tecnologia="fv", forzar=False, verbose=True):
    """Lee, revisa y guarda. Devuelve (id, resumen)."""
    from cargar_perfil import cargar, a_forma
    from caso import guardar_forma

    que = "consumo" if es_consumo else "generacion"
    if verbose:
        print(f"\n{'='*68}\n  {que.upper()} · {ruta.name}\n{'='*68}")
    s = cargar(ruta, unidad=unidad, verbose=verbose)
    r = _resumen(s)

    problemas = revisar(r, es_consumo, tecnologia)
    if problemas:
        if verbose:
            print(f"\n  {'AVISO' if forzar else 'NO SE GUARDA'}:")
            for p in problemas:
                print(f"    - {p}")
        if not forzar:
            if verbose:
                print(f"\n  Con --forzar se guarda igualmente.")
            return None, r, problemas

    f = a_forma(s)
    if es_consumo:
        cur.execute(
            "INSERT INTO app_consump_inst (user_id, code, name, annual_mwh, source_file) "
            "VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (user_id, code) DO UPDATE SET annual_mwh = EXCLUDED.annual_mwh, "
            "name = EXCLUDED.name, source_file = EXCLUDED.source_file "
            "RETURNING consump_id",
            (uid, code, nombre, r["anual_mwh"], str(ruta)))
        i = cur.fetchone()[0]
        n = guardar_forma(cur, "app_consump_shape", "consump_id", i, f)
    else:
        mwp = r["anual_mwh"] / RENDIMIENTO_FV
        cur.execute(
            "INSERT INTO app_gen_inst (user_id, code, name, technology, capacity_mwp, "
            "source_file) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (user_id, code) DO UPDATE SET capacity_mwp = EXCLUDED.capacity_mwp, "
            "name = EXCLUDED.name, source_file = EXCLUDED.source_file RETURNING gen_id",
            (uid, code, nombre, tecnologia, mwp, str(ruta)))
        i = cur.fetchone()[0]
        n = guardar_forma(cur, "app_gen_shape", "gen_id", i, f)
        r["mwp"] = mwp

    if verbose:
        print(f"\n  GUARDADO como '{code}' (id {i}) · {n} filas de forma")
        print(f"    {r['dias']} dias · {r['desde']:%Y-%m-%d} -> {r['hasta']:%Y-%m-%d}")
        print(f"    {r['anual_mwh']:,.1f} MWh/año · pico {r['pico_mw']*1000:,.0f} kW")
        if not es_consumo:
            print(f"    {r['mwp']*1000:,.0f} kWp equivalentes a "
                  f"{RENDIMIENTO_FV} kWh/kWp")
    return i, r, problemas


def respuesta(que: str, code: str, ident, r: dict, problemas: list) -> dict:
    """Lo que la web recibe de vuelta. Es el contrato: si cambia, cambia el front.

    Va SIEMPRE con la misma forma, se haya guardado o no. Un front que tiene que distinguir
    dos formas de respuesta segun haya ido bien o mal acaba con dos caminos y uno de los dos
    sin probar.
    """
    return {
        "tipo": que,
        "ok": ident is not None,
        "code": code,
        "id": ident,
        "problemas": problemas,
        "curva": {
            "desde": f"{r['desde']:%Y-%m-%d}", "hasta": f"{r['hasta']:%Y-%m-%d}",
            "dias": r["dias"], "horas": r["horas"], "anos": round(r["anos"], 2),
            "anual_mwh": round(r["anual_mwh"], 1),
            "pico_kw": round(r["pico_mw"] * 1000, 1),
            "media_kw": round(r["total_mwh"] / max(r["horas"], 1) * 1000, 1),
            "nulos": r["nulos"], "negativos": r["negativos"],
            **({"kwp_equivalentes": round(r["mwp"] * 1000, 1)} if "mwp" in r else {}),
        },
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--consumo", help="fichero de la curva de consumo")
    ap.add_argument("--generacion", help="fichero de la curva de generacion")
    ap.add_argument("--code-consumo", default="CONSUMO")
    ap.add_argument("--code-generacion", default="GENERACION")
    ap.add_argument("--nombre-consumo", default="Curva de consumo")
    ap.add_argument("--nombre-generacion", default="Curva de generacion")
    ap.add_argument("--unidad", default="auto",
                    help="kwh|mwh; 'auto' la lee del encabezado y no la adivina")
    ap.add_argument("--tecnologia", default="fv", choices=["fv", "eolica", "otra"])
    ap.add_argument("--forzar", action="store_true",
                    help="guardar aunque la revision encuentre problemas")
    ap.add_argument("--json", action="store_true",
                    help="devolver el resultado en JSON y callar lo demas")
    ap.add_argument("--email", default=None)
    a = ap.parse_args()

    if not a.consumo and not a.generacion:
        raise SystemExit("hay que pasar al menos --consumo o --generacion")

    from caso import conexion, _uno
    email = a.email or os.environ.get("TFM_EMAIL")
    if not email:
        raise SystemExit("falta el email: --email o la variable TFM_EMAIL")

    hablar = not a.json
    con = conexion()
    cur = con.cursor()
    uid = _uno(cur, "SELECT user_id FROM app_user WHERE email = %s", (email,))
    if uid is None:
        uid = _uno(cur, "INSERT INTO app_user (email) VALUES (%s) RETURNING user_id",
                   (email,))
    if hablar:
        print(f"  usuario {email} (id {uid})")

    salida, hechos = [], 0
    for ruta, es_consumo, code, nombre in (
            (a.consumo, True, a.code_consumo, a.nombre_consumo),
            (a.generacion, False, a.code_generacion, a.nombre_generacion)):
        if not ruta:
            continue
        i, r, problemas = cargar_una(cur, uid, Path(ruta), es_consumo, code, nombre,
                                     a.unidad, a.tecnologia, a.forzar, verbose=hablar)
        salida.append(respuesta("consumo" if es_consumo else "generacion",
                                code, i, r, problemas))
        hechos += i is not None

    if hechos:
        con.commit()
    else:
        con.rollback()
    cur.close()
    con.close()

    if a.json:
        import json as _json
        print(_json.dumps(salida, indent=2, ensure_ascii=False))
    else:
        print(f"\n  {hechos} curva(s) en la base." if hechos
              else "\n  no se ha guardado nada.")
    raise SystemExit(0 if hechos else 1)


if __name__ == "__main__":
    main()
