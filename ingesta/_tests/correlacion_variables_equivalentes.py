#!/usr/bin/env python3
"""
TFM Energia UCM — Correlacion entre variables equivalentes de distintas tablas
=============================================================================
Diagnostico previo al EDA y al feature engineering.

PARA QUE SIRVE
La base tiene diez tablas horarias alimentadas por DOS operadores distintos
(REE via ESIOS y ENTSO-E) mas programas y previsiones. Muchas columnas miden
lo mismo, o casi. Antes de modelizar hay que saber cuales:

  - DUPLICADAS: correlacion practicamente 1 y misma escala. Meter las dos al
    modelo introduce colinealidad; hay que elegir una y documentar por que.
    Caso ya verificado: gen_renovables_prev_mw = eolica + fotovoltaica exacto
    en 58.150 horas, con lo que el agregado no aporta informacion.

  - CON SIGNO OPUESTO: correlacion cercana a -1. Los saldos de interconexion
    son el caso critico: ENTSO-E usa positivo = importacion hacia España,
    verificado empiricamente, pero ESIOS no tiene obligacion de usar el mismo
    convenio. Mezclarlas sin advertirlo produce un modelo que aprende el signo
    equivocado en las horas de exportacion.

  - CON PROBLEMA DE ESCALA: cociente de medias cercano a 4, 12 o 1/4. Es la
    firma de una agregacion mal hecha (SUM donde tocaba AVERAGE) o de confundir
    potencia con energia.

  - DIVERGENTES: correlacion baja donde se esperaba alta. Ahi hay un hallazgo:
    o las dos columnas no miden lo mismo, o una tiene un problema. Caso ya
    documentado: entsoe_gen_data.solar_mw incluye termosolar (PSR B16) y
    esios_gen.ree_gsolar_mw es solo fotovoltaica, asi que correlacionan 0,986
    pero ENTSO-E puede marcar 650 MW de noche donde ESIOS marca 15.

COMO FUNCIONA
Dos vias complementarias:

  1. PAREJAS CURADAS: la lista de abajo, con el motivo de cada comparacion.
     Solo se evaluan las parejas cuyas dos columnas existan de verdad, asi que
     el script no falla si una columna cambio de nombre.

  2. DESCUBRIMIENTO AUTOMATICO: agrupa las columnas numericas de las tablas
     horarias por palabra clave semantica (solar, eolica, nuclear, demanda,
     saldo...) y correlaciona todas las combinaciones entre tablas distintas.
     Sirve para encontrar equivalencias que no estan en la lista curada.

El calculo se hace en SQL (corr, regr_slope, avg) sobre el JOIN por la columna
temporal: no descarga datos, asi que da igual el tamaño de las tablas.

NO ESCRIBE NADA. Solo lee.

Uso
---
  python correlacion_variables_equivalentes.py
  python correlacion_variables_equivalentes.py --solo-curadas
  python correlacion_variables_equivalentes.py --desde 2021-01-01
  python correlacion_variables_equivalentes.py --csv informe.csv
  python correlacion_variables_equivalentes.py --min-corr 0.5   # umbral de aviso
"""

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2

sys.path.append(str(Path(__file__).resolve().parent.parent))
from config import load_config

# --------------------------------------------------------------------------
#  Parejas curadas: (tabla_a, col_a, tabla_b, col_b, esperado, motivo)
#
#  `esperado` orienta la lectura del resultado, no lo condiciona:
#     "identica"  -> deberian ser la misma cifra
#     "alta"      -> miden lo mismo desde fuentes distintas
#     "prevision" -> una es previsión y la otra la realidad
#     "signo"     -> hay que comprobar el convenio de signo
#     "agregado"  -> una contiene a la otra
# --------------------------------------------------------------------------
CURADAS = [
    # ── Demanda ───────────────────────────────────────────────────────────
    ("esios_forecast_da", "demanda_prev_mw",
     "esios_forecast_da", "demanda_mercado_prev_mw", "alta",
     "1775 incluye estimacion de autoconsumo desde 11/12/2025; 2563 no. "
     "La diferencia deberia ser el autoconsumo, no ruido"),

    ("esios_forecast_da", "demanda_prev_mw",
     "entsoe_forecast_da", "load_forecast_mw", "alta",
     "DOS previsiones de demanda de operadores distintos para el mismo dia. "
     "Si correlacionan 0,999 una es redundante"),

    ("esios_forecast_da", "demanda_prev_mw",
     "entsoe_load_inter", "actual_load_mw", "prevision",
     "Prevision D+1 contra demanda real. El error de esta prevision es una "
     "referencia de que precision es alcanzable"),

    # ── Solar ─────────────────────────────────────────────────────────────
    ("entsoe_gen_data", "solar_mw",
     "esios_gen", "ree_gsolar_mw", "alta",
     "PSR B16 de ENTSO-E incluye FV + termosolar; el 1295 de ESIOS es solo FV. "
     "NO son intercambiables aunque correlacionen alto"),

    ("esios_forecast_da", "gen_solar_pv_prev_mw",
     "esios_gen", "ree_gsolar_mw", "prevision",
     "Prevision FV D+1 contra FV real, misma fuente y misma tecnologia"),

    ("esios_forecast_da", "gen_solartermica_prev_mw",
     "esios_gen", "ree_gsolter_mw", "prevision",
     "Prevision termosolar contra termosolar real"),

    # ── Eolica ────────────────────────────────────────────────────────────
    ("entsoe_gen_data", "wind_mw",
     "esios_gen", "ree_gwind_mw", "identica",
     "Eolica peninsular medida por los dos operadores. Sin desglose de "
     "tecnologias que los separe, deberian coincidir"),

    ("esios_forecast_da", "gen_wind_prev_mw",
     "esios_gen", "ree_gwind_mw", "prevision",
     "Prevision eolica D+1 contra eolica real: probablemente el predictor mas "
     "importante del precio"),

    # ── Nuclear y termicas ────────────────────────────────────────────────
    ("entsoe_gen_data", "nuclear_mw",
     "esios_gen", "ree_gnuclear_mw", "identica",
     "Nuclear no tiene ambiguedad de clasificacion: debe coincidir casi exacto"),

    ("entsoe_gen_data", "gas_mw",
     "esios_gen", "ree_gccgas_mw", "agregado",
     "B04 de ENTSO-E agrupa ciclo combinado Y cogeneracion (P.O. 3.1 Anexo II); "
     "el 550 de ESIOS es ciclo combinado PURO. Verificado 17/08: la suma con "
     "ree_gotherthermal_mw cuadra al 3%. Importa porque el CC es la tecnologia "
     "marginal y la cogeneracion es inflexible"),

    # ── Hidraulica y bombeo ───────────────────────────────────────────────
    ("esios_gen", "ree_ghidro_mw",
     "entsoe_gen_data", "hydro_reservoir_mw", "agregado",
     "El 546 esta NETEADO de bombeo y puede ser negativo a mediodia; ENTSO-E "
     "separa embalse y fluyente. Correlacion baja aqui es esperada"),

    ("esios_gen", "ree_gpumping_mw",
     "entsoe_gen_data", "pumping_gen_mw", "identica",
     "Turbinacion de bombeo medida por los dos operadores"),

    ("esios_gen", "ree_cpumping_mw",
     "entsoe_gen_data", "pumping_cons_mw", "signo",
     "Consumo de bombeo. El 2078 esta marcado como NEGATIVO en el pipeline de "
     "ESIOS; hay que comprobar si ENTSO-E usa el mismo signo"),

    # ── Interconexiones: EL PUNTO CRITICO ─────────────────────────────────
    ("esios_load_inter", "saldo_francia_mw",
     "entsoe_load_inter", "net_flow_fr_mw", "signo",
     "PENDIENTE DECLARADO: ENTSO-E usa positivo = importacion hacia España "
     "(verificado el 10-ago-2026). ESIOS es otro operador y no tiene por que "
     "coincidir. Una correlacion cercana a -1 significa convenio invertido, y "
     "mezclarlas sin advertirlo daria un modelo con el signo al reves"),

    ("esios_load_inter", "saldo_portugal_mw",
     "entsoe_load_inter", "net_flow_pt_mw", "signo",
     "Mismo contraste de convenio para la frontera portuguesa"),

    ("esios_load_inter", "saldo_portugal_mw",
     "esios_load_inter", "saldo_portugal_exp_mw", "agregado",
     "PENDIENTE DECLARADO: el 561 (solo exportacion) es parcialmente redundante "
     "con el 10208 (saldo neto). Si la correlacion es muy alta, sobra una"),

    # ── NTC ───────────────────────────────────────────────────────────────
    ("esios_forecast_da", "ntc_fr_imp_prev_mw",
     "entsoe_load_inter", "ntc_imp_fr_mw", "alta",
     "NTC de importacion con Francia por las dos fuentes. Las de ESIOS estan "
     "completas desde 2020-11 y tienen 6 columnas; las de ENTSO-E tienen 4 y "
     "un hueco desde dic-2023"),

    # ── Precio ────────────────────────────────────────────────────────────
    ("spot_price", "es_price_eur_mwh",
     "spot_price", "pt_price_eur_mwh", "alta",
     "MIBEL: España y Portugal comparten mercado y el precio coincide salvo "
     "en horas de congestion. La frecuencia de desacoplamiento es un dato "
     "para la memoria"),

    ("spot_price", "es_price_eur_mwh",
     "entsoe_forecast_da", "price_fr", "alta",
     "Spread Francia-España: base del arbitraje transfronterizo"),
]

# Palabras clave para el descubrimiento automatico. Cada grupo reune columnas
# que probablemente midan la misma magnitud fisica.
GRUPOS = {
    "solar":      ("solar", "gsolar", "fotovolt", "gsolter", "termica_sol"),
    "eolica":     ("wind", "eolic", "gwind"),
    "nuclear":    ("nuclear",),
    "gas":        ("gas_mw", "gccgas", "ccgt", "ciclo"),
    "carbon":     ("coal", "gcoal", "hulla"),
    "hidraulica": ("hydro", "ghidro", "hidro"),
    "bombeo":     ("pumping", "bombeo"),
    "demanda":    ("demanda", "load", "consumo"),
    "saldo":      ("saldo", "net_flow", "flow_"),
    "ntc":        ("ntc",),
    "renovable":  ("renov", "grenew", "renew"),
    "precio":     ("price", "precio"),
}

EXCLUIR_COLS = {"updated_at", "created_at", "datetime", "fecha", "date", "ts"}


# --------------------------------------------------------------------------

def inventario(conn):
    """{tabla: {"tiempo": col, "num": [cols numericas]}} de las tablas base."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.table_name, c.column_name, c.data_type
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_name = c.table_name AND t.table_schema = c.table_schema
            WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
            ORDER BY c.table_name, c.ordinal_position
        """)
        filas = cur.fetchall()

    inv = defaultdict(lambda: {"tiempo": None, "num": []})
    for tabla, col, tipo in filas:
        if tipo.startswith("timestamp") or tipo == "date":
            # La columna temporal es la primera de tipo fecha que no sea de auditoria
            if inv[tabla]["tiempo"] is None and col not in ("updated_at", "created_at"):
                inv[tabla]["tiempo"] = col
        elif tipo in ("numeric", "double precision", "real", "integer", "bigint"):
            if col not in EXCLUIR_COLS:
                inv[tabla]["num"].append(col)
    return dict(inv)


def granularidad(inv, tabla):
    """'horaria' si la columna temporal es timestamptz, 'diaria' si es date."""
    t = inv[tabla]["tiempo"]
    return "diaria" if t in ("fecha", "date") else "horaria"


def comparar(conn, inv, ta, ca, tb, cb, desde):
    """
    Estadisticos de la pareja. Devuelve dict o None si no es comparable.

    Se calcula en SQL para no descargar 58.000 filas por pareja. regr_slope da
    la pendiente de la regresion: junto al cociente de medias distingue un
    problema de escala (pendiente ~4) de un desplazamiento constante
    (pendiente ~1 con medias distintas).
    """
    if ta not in inv or tb not in inv:
        return None
    if ca not in inv[ta]["num"] or cb not in inv[tb]["num"]:
        return None
    if granularidad(inv, ta) != granularidad(inv, tb):
        return None

    t_a, t_b = inv[ta]["tiempo"], inv[tb]["tiempo"]
    filtro = f"AND a.{t_a} >= %s" if desde else ""
    args = (desde,) if desde else ()

    if ta == tb:
        sql = f"""
            SELECT count(*),
                   corr({ca}, {cb}),
                   avg({ca}), avg({cb}),
                   avg({ca} - {cb}), stddev({ca} - {cb}),
                   regr_slope({ca}, {cb}),
                   count(*) FILTER (WHERE abs({ca} - {cb}) > 0.01)
            FROM {ta} a
            WHERE {ca} IS NOT NULL AND {cb} IS NOT NULL {filtro}
        """
    else:
        sql = f"""
            SELECT count(*),
                   corr(a.{ca}, b.{cb}),
                   avg(a.{ca}), avg(b.{cb}),
                   avg(a.{ca} - b.{cb}), stddev(a.{ca} - b.{cb}),
                   regr_slope(a.{ca}, b.{cb}),
                   count(*) FILTER (WHERE abs(a.{ca} - b.{cb}) > 0.01)
            FROM {ta} a
            JOIN {tb} b ON b.{t_b} = a.{t_a}
            WHERE a.{ca} IS NOT NULL AND b.{cb} IS NOT NULL {filtro}
        """

    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            n, r, ma, mb, dif, desv, pend, n_dif = cur.fetchone()
    except Exception as e:
        conn.rollback()
        return {"error": str(e).split("\n")[0][:70]}

    if not n:
        return {"n": 0}

    f = lambda x: float(x) if x is not None else None
    return {"n": n, "r": f(r), "media_a": f(ma), "media_b": f(mb),
            "dif": f(dif), "desv": f(desv), "pendiente": f(pend),
            "n_distintos": n_dif}


def veredicto(res, esperado=None):
    """Clasificacion legible. El orden importa: se comprueba lo grave primero."""
    if res is None:
        return "no comparable"
    if "error" in res:
        return f"ERROR {res['error']}"
    if res["n"] == 0:
        return "sin solape de datos"
    if res["n"] < 100:
        return f"COBERTURA INSUFICIENTE (n={res['n']})"

    r, ratio = res["r"], None
    if res["media_b"] not in (None, 0):
        ratio = res["media_a"] / res["media_b"]

    if r is None:
        return "correlacion no calculable (varianza nula)"

    # Varianza nula en una de las dos: una constante no es una serie
    if res["desv"] == 0 and res["dif"] == 0:
        return "IDENTICAS (columna duplicada)"

    if r < -0.90:
        return "SIGNO OPUESTO — revisar convenio antes de mezclar"

    if ratio is not None and r > 0.95:
        for k in (4, 12, 0.25):
            if abs(ratio - k) < 0.06 * k:
                return f"ESCALA x{k:g} — agregacion mal hecha"

    if r > 0.9999 and res["n_distintos"] == 0:
        return "IDENTICAS (columna duplicada)"
    if r > 0.999:
        return "REDUNDANTE — elegir una para el modelo"
    if r > 0.98:
        return "muy alta — diferencia documentable"
    if r > 0.90:
        return "alta — consistente"
    if r > 0.70:
        return "moderada — miden cosas distintas"
    if abs(r) < 0.3:
        return "SIN RELACION — revisar si se esperaba equivalencia"
    return "baja — investigar"


def linea(res):
    if res is None or "error" in res or not res.get("n"):
        return ""
    r = f"{res['r']:+.4f}" if res["r"] is not None else "   n/a"
    return (f"n={res['n']:>6}  r={r}  "
            f"medias {res['media_a']:>9.2f} / {res['media_b']:>9.2f}  "
            f"dif={res['dif']:>+9.2f}  pend={res['pendiente'] or 0:>6.3f}")


# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--desde", help="acotar el analisis desde esta fecha")
    p.add_argument("--solo-curadas", action="store_true",
                   help="omitir el descubrimiento automatico")
    p.add_argument("--csv", help="volcar el resultado a CSV")
    p.add_argument("--min-corr", type=float, default=0.90,
                   help="umbral para destacar parejas en el descubrimiento")
    args = p.parse_args()

    _, db = load_config()
    conn = psycopg2.connect(**db)
    inv = inventario(conn)

    print("=" * 100)
    print("CORRELACION ENTRE VARIABLES EQUIVALENTES")
    if args.desde:
        print(f"Periodo: desde {args.desde}")
    print(f"Tablas base detectadas: {len(inv)}")
    print("=" * 100)

    resultados = []

    # ── 1. Parejas curadas ────────────────────────────────────────────────
    print("\n\n### 1. PAREJAS CURADAS\n")
    for ta, ca, tb, cb, esp, motivo in CURADAS:
        res = comparar(conn, inv, ta, ca, tb, cb, args.desde)
        v = veredicto(res, esp)
        print(f"{ta}.{ca}")
        print(f"  vs {tb}.{cb}   [esperado: {esp}]")
        if res and res.get("n"):
            print(f"  {linea(res)}")
        print(f"  -> {v}")
        print(f"     {motivo}")
        print()
        resultados.append({"origen": "curada", "tabla_a": ta, "col_a": ca,
                           "tabla_b": tb, "col_b": cb, "esperado": esp,
                           "veredicto": v,
                           **{k: v2 for k, v2 in (res or {}).items()
                              if k != "error"}})

    # ── 2. Descubrimiento automatico ──────────────────────────────────────
    if not args.solo_curadas:
        print("\n### 2. DESCUBRIMIENTO AUTOMATICO POR GRUPO SEMANTICO")
        print("    (solo parejas entre tablas DISTINTAS de igual granularidad)\n")

        horarias = [t for t in inv if granularidad(inv, t) == "horaria"
                    and inv[t]["tiempo"]]
        ya = {(x[0], x[1], x[2], x[3]) for x in
              [(c[0], c[1], c[2], c[3]) for c in CURADAS]}

        for grupo, claves in GRUPOS.items():
            cands = []
            for t in horarias:
                for c in inv[t]["num"]:
                    if any(k in c.lower() for k in claves):
                        cands.append((t, c))
            if len(cands) < 2:
                continue

            destacadas = []
            for i in range(len(cands)):
                for j in range(i + 1, len(cands)):
                    ta, ca = cands[i]
                    tb, cb = cands[j]
                    if ta == tb:
                        continue
                    if (ta, ca, tb, cb) in ya or (tb, cb, ta, ca) in ya:
                        continue
                    res = comparar(conn, inv, ta, ca, tb, cb, args.desde)
                    if not res or not res.get("n") or res.get("r") is None:
                        continue
                    # Solo interesa lo muy correlacionado o el signo invertido
                    if abs(res["r"]) < args.min_corr:
                        continue
                    v = veredicto(res)
                    destacadas.append((ta, ca, tb, cb, res, v))
                    resultados.append({"origen": "auto", "grupo": grupo,
                                       "tabla_a": ta, "col_a": ca,
                                       "tabla_b": tb, "col_b": cb,
                                       "veredicto": v,
                                       **{k: x for k, x in res.items()
                                          if k != "error"}})

            if destacadas:
                print(f"  --- {grupo.upper()} ({len(cands)} columnas candidatas) ---")
                destacadas.sort(key=lambda x: -abs(x[4]["r"]))
                for ta, ca, tb, cb, res, v in destacadas[:12]:
                    print(f"    {ta}.{ca}")
                    print(f"      vs {tb}.{cb}")
                    print(f"      {linea(res)}")
                    print(f"      -> {v}")
                print()

    # ── 3. Resumen ────────────────────────────────────────────────────────
    print("\n### 3. RESUMEN — lo que hay que decidir antes del feature engineering\n")
    graves = defaultdict(list)
    for r in resultados:
        v = r["veredicto"]
        for clave in ("SIGNO OPUESTO", "IDENTICAS", "REDUNDANTE", "ESCALA",
                      "SIN RELACION", "COBERTURA INSUFICIENTE"):
            if clave in v:
                graves[clave].append(r)
                break

    if not graves:
        print("  Ninguna pareja requiere decision: no hay duplicadas exactas,")
        print("  ni convenios de signo opuestos, ni problemas de escala.")
    for clave in ("SIGNO OPUESTO", "ESCALA", "IDENTICAS", "REDUNDANTE",
                  "SIN RELACION", "COBERTURA INSUFICIENTE"):
        if clave not in graves:
            continue
        print(f"  {clave} ({len(graves[clave])}):")
        for r in graves[clave]:
            rr = f"{r['r']:+.4f}" if r.get("r") is not None else "n/a"
            print(f"    r={rr}  {r['tabla_a']}.{r['col_a']}  <->  "
                  f"{r['tabla_b']}.{r['col_b']}")
        print()

    if args.csv:
        campos = sorted({k for r in resultados for k in r})
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=campos)
            w.writeheader()
            w.writerows(resultados)
        print(f"  CSV escrito: {args.csv}  ({len(resultados)} parejas)")

    conn.close()


if __name__ == "__main__":
    main()
