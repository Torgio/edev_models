#!/usr/bin/env python3
"""
TFM Energia UCM - Series continuas de futuros: EUA front December y TTF M+1
Origen: trayport_daily_ohlc  ->  Destino: commodities

QUE PROBLEMA RESUELVE
Un futuro no es una serie temporal: es un contrato que nace, se negocia y
vence. Para tener una serie continua hay que ir empalmando contratos, y la
regla de empalme es una decision metodologica que debe quedar escrita, no
improvisada en una consulta suelta.

REGLA UNICA PARA LAS DOS SERIES
En cada fecha se toma el vencimiento MAS PROXIMO que tenga cotizacion ese dia,
entre los contratos candidatos. Lo que cambia es quien es candidato:

  EUA - front December: solo los diciembres, con anio de entrega >= anio de la
        fecha. En enero de 2020 el mas proximo es Dic-20; cuando el Dic-20
        vence (mediados de diciembre) el mas proximo pasa a ser Dic-21.
        El diciembre es la referencia del mercado de emisiones: el plazo de
        cumplimiento es abril del anio siguiente y ahi se concentra la
        liquidez. Verificado empiricamente: el contrato mas negociado de cada
        anio de la serie es siempre el Dic-N de ese anio.

  TTF - M+1: los mensuales con mes de entrega POSTERIOR al mes de la fecha.
        Normalmente da el mes siguiente; cuando ese mensual vence (unos dias
        antes de empezar el mes de entrega) pasa solo al M+2.

La misma regla cubre el vencimiento sin necesidad de escribir a mano ninguna
fecha de expiracion, que es lo que se rompe cuando el calendario cambia.

POR QUE EL RELEVO IMPORTA (medido, no supuesto)
Empalmando en el vencimiento en vez de en enero, los saltos artificiales del
EUA bajan de 5,84 EUR de media (5,0x la variacion diaria tipica) a 2,42 EUR
(2,1x). El salto residual es prima de contango, no movimiento de mercado.

QUE NO HACE ESTE SCRIPT
No ajusta el salto de roll. Escribe la serie CRUDA, que es lo que realmente
cotizo y por tanto lo trazable. El ajuste retroactivo es una transformacion
de modelizacion y va en el feature engineering, no en la ingesta. El script
IMPRIME los saltos detectados para que queden documentados: en los dias de
solapamiento ambos contratos cotizan, asi que la prima es medible de forma
exacta y el ajuste puede hacerse sin estimaciones.

FUENTE DEL PRECIO
Se usa `cierre` (ultimo precio negociado del dia). trayport_daily_ohlc NO
tiene columna de settlement, asi que NO debe describirse como precio de
liquidacion en la memoria: el settlement es el precio oficial de la camara y
no esta en estos datos. El `vwap` esta disponible como alternativa mas robusta
a operaciones aisladas al cierre (--precio vwap).

NOTA SOBRE FUGA DE INFORMACION
Son precios de futuros con negociacion continua: el cierre del dia D-1 se
conoce antes de las 12:00 del dia D, asi que son features validos sin lag.
Aun asi, usar el cierre del dia anterior es lo prudente.

commodities tiene una fila por DIA NATURAL (incluye fines de semana), asi que
esto es un UPDATE, nunca un INSERT. Los futuros solo cotizan en dias de
mercado: las columnas quedaran con nulos en fines de semana y festivos. Eso
no es un hueco, es la naturaleza de la fuente — y contrasta con co2_ets, que
viene de un indicador MENSUAL de ESIOS repetido todos los dias del mes.

Uso
---
  python commodities_futuros_history.py                  # simulacion
  python commodities_futuros_history.py --ejecutar
  python commodities_futuros_history.py --ejecutar --commodity EUA
  python commodities_futuros_history.py --verificar      # estado actual
"""

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

MESES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
         "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
MES_NUM = {m: i + 1 for i, m in enumerate(MESES)}

ORIGEN = "trayport_daily_ohlc"
DESTINO = "commodities"

SERIES = {
    "EUA": {"columna": "co2_eua_dec", "unidad": "EUR/t",
            "descripcion": "front December"},
    "TTF": {"columna": "gas_ttf_m1", "unidad": "EUR/MWh",
            "descripcion": "M+1 (mes siguiente)"},
}


# --------------------------------------------------------------------------
#  Parseo de contratos
# --------------------------------------------------------------------------

def entrega(periodo: str):
    """'Dic-26' -> (2026, 12). Devuelve None si el formato no encaja."""
    try:
        mes, anio = periodo.split("-")
        m = MES_NUM.get(mes.strip())
        if m is None:
            return None
        return (2000 + int(anio), m)
    except (ValueError, AttributeError):
        return None


def es_candidato(commodity: str, ent, f: date) -> bool:
    """
    Un contrato es candidato en la fecha f si su vencimiento aun no ha pasado
    conceptualmente. La diferencia entre las dos series esta solo aqui.
    """
    ey, em = ent
    if commodity == "EUA":
        # solo diciembres, del anio en curso en adelante
        return em == 12 and ey >= f.year
    # TTF: cualquier mensual con entrega posterior al mes en curso
    return (ey, em) > (f.year, f.month)


# --------------------------------------------------------------------------
#  Construccion de la serie
# --------------------------------------------------------------------------

def construir(conn, commodity: str, precio_col: str):
    """
    Devuelve (serie, rolls):
      serie = [(fecha, valor, periodo)] ordenada
      rolls = [(fecha_ant, valor_ant, per_ant, fecha, valor, per, prima_medida)]

    prima_medida es la diferencia entre los dos contratos EL MISMO DIA, cuando
    ambos cotizan. Es el unico valor limpio de la prima de contango: comparar
    el ultimo dia de un contrato con el primero del siguiente mezcla la prima
    con el movimiento real de mercado de esos dias.
    """
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT fecha, periodo, {precio_col}
            FROM {ORIGEN}
            WHERE commodity = %s AND {precio_col} IS NOT NULL
            ORDER BY fecha
        """, (commodity,))
        filas = cur.fetchall()

    # fecha -> {(anio, mes) entrega: (valor, periodo)}
    por_fecha = defaultdict(dict)
    descartados = set()
    for f, periodo, valor in filas:
        ent = entrega(periodo)
        if ent is None:
            descartados.add(periodo)
            continue
        por_fecha[f][ent] = (float(valor), periodo)

    if descartados:
        print(f"    AVISO: periodos con formato no reconocido, ignorados: "
              f"{sorted(descartados)[:6]}")

    serie = []
    for f in sorted(por_fecha):
        cands = [(ent, v, p) for ent, (v, p) in por_fecha[f].items()
                 if es_candidato(commodity, ent, f)]
        if not cands:
            continue
        cands.sort(key=lambda x: x[0])       # vencimiento mas proximo primero
        ent, valor, periodo = cands[0]
        serie.append((f, valor, periodo, por_fecha[f]))

    # Detectar los cambios de contrato y medir la prima el mismo dia
    rolls = []
    for i in range(1, len(serie)):
        f_ant, v_ant, p_ant, _ = serie[i - 1]
        f_act, v_act, p_act, disp_act = serie[i]
        if p_act == p_ant:
            continue
        # prima medible: valor del contrato nuevo menos el del viejo, en el
        # ultimo dia en que el viejo aun cotizaba
        _, _, _, disp_ant = serie[i - 1]
        ent_nuevo = entrega(p_act)
        prima = None
        if ent_nuevo in disp_ant:
            prima = disp_ant[ent_nuevo][0] - v_ant
        rolls.append((f_ant, v_ant, p_ant, f_act, v_act, p_act, prima))

    return [(f, v, p) for f, v, p, _ in serie], rolls


# --------------------------------------------------------------------------
#  Informe
# --------------------------------------------------------------------------

def informe(commodity, cfg, serie, rolls):
    print(f"\n=== {commodity} — {cfg['descripcion']} ({cfg['unidad']}) ===")
    if not serie:
        print("  SIN DATOS")
        return
    print(f"  dias de mercado : {len(serie)}")
    print(f"  rango           : {serie[0][0]} .. {serie[-1][0]}")
    vals = [v for _, v, _ in serie]
    print(f"  precio          : {min(vals):.2f} .. {max(vals):.2f}")
    print(f"  cambios de contrato: {len(rolls)}")

    por_anio = defaultdict(int)
    for f, _, _ in serie:
        por_anio[f.year] += 1
    print("  dias por anio   : " +
          "  ".join(f"{a}={n}" for a, n in sorted(por_anio.items())))

    if not rolls:
        return

    # Variacion diaria tipica, para dimensionar los saltos
    difs = [abs(serie[i][1] - serie[i - 1][1]) for i in range(1, len(serie))]
    tipica = sum(difs) / len(difs) if difs else 0

    mostrar = rolls if len(rolls) <= 12 else rolls[:6] + rolls[-6:]
    print(f"\n  SALTOS EN EL CAMBIO DE CONTRATO"
          f"{' (primeros y ultimos 6)' if len(rolls) > 12 else ''}")
    print(f"  {'de':<12} {'a':<12} {'salto':>8} {'prima medida':>13}  contratos")
    print("  " + "-" * 68)
    saltos = []
    for f_a, v_a, p_a, f_b, v_b, p_b, prima in mostrar:
        s = v_b - v_a
        saltos.append(abs(s))
        pm = f"{prima:+.2f}" if prima is not None else "no solapan"
        print(f"  {str(f_a):<12} {str(f_b):<12} {s:>+8.2f} {pm:>13}  "
              f"{p_a} -> {p_b}")

    todos = [abs(v_b - v_a) for _, v_a, _, _, v_b, _, _ in rolls]
    medidas = [p for *_, p in rolls if p is not None]
    print(f"\n  variacion diaria tipica : {tipica:.2f}")
    print(f"  salto medio en el roll  : {sum(todos)/len(todos):.2f}"
          f"  ({sum(todos)/len(todos)/tipica:.1f}x la tipica)" if tipica else "")
    if medidas:
        print(f"  prima medida el mismo dia: {sum(medidas)/len(medidas):+.2f} "
              f"en {len(medidas)}/{len(rolls)} cambios")
        print("  -> el ajuste de roll puede ser EXACTO en esos cambios: la parte")
        print("     del salto que no es prima es movimiento real de mercado.")
    else:
        print("  -> ningun cambio tiene solapamiento: la prima solo puede")
        print("     estimarse, no medirse. Documentarlo como tal.")


# --------------------------------------------------------------------------
#  Escritura
# --------------------------------------------------------------------------

def escribir(conn, columna, serie, ejecutar: bool):
    """UPDATE sobre commodities. Nunca INSERT: las filas ya existen."""
    if not serie:
        return 0, 0

    registros = [(f, v) for f, v, _ in serie]

    # Fechas de la serie que NO existen como fila en commodities. Se comprueba
    # en Python en vez de con un VALUES gigante: mas simple y sin construir SQL
    # a mano, que es donde se cuelan los errores de escapado.
    with conn.cursor() as cur:
        cur.execute(f"SELECT fecha FROM {DESTINO} WHERE fecha BETWEEN %s AND %s",
                    (serie[0][0], serie[-1][0]))
        existentes = {r[0] for r in cur.fetchall()}

    faltan = [f for f, _ in registros if f not in existentes]
    huerfanas = len(faltan)
    if huerfanas:
        print(f"    AVISO: {huerfanas} fechas de la serie no existen como fila "
              f"en {DESTINO} y no se escribiran.")
        print(f"    Primeras: {[str(f) for f in faltan[:5]]}")
        print(f"    {DESTINO} deberia tener una fila por dia natural; revisar "
              f"antes de dar la carga por completa.")
        registros = [(f, v) for f, v in registros if f in existentes]

    if not ejecutar:
        return len(registros) - huerfanas, huerfanas

    with conn.cursor() as cur:
        execute_values(cur, f"""
            UPDATE {DESTINO} AS c
               SET {columna} = v.valor
              FROM (VALUES %s) AS v(f, valor)
             WHERE c.fecha = v.f
        """, registros, template="(%s, %s::numeric)", page_size=1000)
        n = cur.rowcount
    conn.commit()
    return n, huerfanas


def verificar(conn):
    cols = ", ".join(f"count({c['columna']}) AS {c['columna']}"
                     for c in SERIES.values())
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT count(*), min(fecha), max(fecha),
                   count(co2_ets), count(gas_ttf), {cols}
            FROM {DESTINO}
        """)
        n, d, h, n_ets, n_ttf_yahoo, n_eua, n_ttf = cur.fetchone()

    print(f"\nESTADO DE {DESTINO}")
    print(f"  filas (dias naturales): {n}   {d} .. {h}")
    print(f"  co2_ets      (ESIOS 1391, mensual)  : {n_ets}")
    print(f"  gas_ttf      (Yahoo TTF=F)          : {n_ttf_yahoo}")
    print(f"  co2_eua_dec  (Trayport front Dec)   : {n_eua}")
    print(f"  gas_ttf_m1   (Trayport M+1)         : {n_ttf}")

    # Contraste regulatorio vs mercado, SOLO en dias con ambos datos
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT extract(year from fecha)::int, count(*),
                   round(avg(co2_ets),2), round(avg(co2_eua_dec),2),
                   round(avg(co2_eua_dec - co2_ets),2),
                   round((avg(co2_eua_dec)/avg(co2_ets) - 1)*100, 1)
            FROM {DESTINO}
            WHERE co2_ets IS NOT NULL AND co2_eua_dec IS NOT NULL
            GROUP BY 1 ORDER BY 1
        """)
        filas = cur.fetchall()

    if filas:
        print("\nCO2: referencia regulatoria (ESIOS 1391) vs mercado (EUA Dic)")
        print(f"  {'anio':>5} {'dias':>5} {'ESIOS':>8} {'EUA Dic':>8} "
              f"{'dif EUR':>8} {'dif %':>7}")
        print("  " + "-" * 46)
        for a, nd, e, t, de, dp in filas:
            print(f"  {a:>5} {nd:>5} {e:>8} {t:>8} {de:>8} {dp:>7}")
        print("  El 1391 es una referencia regulatoria mensual y suavizada,")
        print("  no un cierre diario de EUA. La desviacion es el hallazgo.")


# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ejecutar", action="store_true",
                   help="escribir en la BD (sin esto, solo simula)")
    p.add_argument("--commodity", choices=["EUA", "TTF", "ambas"],
                   default="ambas")
    p.add_argument("--precio", choices=["cierre", "vwap"], default="cierre",
                   help="columna de precio de origen (default cierre)")
    p.add_argument("--verificar", action="store_true",
                   help="solo mostrar el estado de commodities y salir")
    args = p.parse_args()

    _, db = load_config()
    conn = psycopg2.connect(**db)

    print("=" * 72)
    print(f"Series continuas de futuros: {ORIGEN} -> {DESTINO}")
    print(f"Precio: {args.precio}   "
          f"MODO: {'ESCRITURA' if args.ejecutar else 'SIMULACION'}")
    print("=" * 72)

    if args.verificar:
        verificar(conn)
        conn.close()
        return

    objetivo = SERIES if args.commodity == "ambas" else \
        {args.commodity: SERIES[args.commodity]}

    total = 0
    for com, cfg in objetivo.items():
        serie, rolls = construir(conn, com, args.precio)
        informe(com, cfg, serie, rolls)
        n, huerf = escribir(conn, cfg["columna"], serie, args.ejecutar)
        verbo = "actualizadas" if args.ejecutar else "se actualizarian"
        print(f"\n  {n} filas {verbo} en {DESTINO}.{cfg['columna']}")
        total += n

    print("\n" + "=" * 72)
    if args.ejecutar:
        print(f"Escritas {total} filas en total.")
        verificar(conn)
    else:
        print(f"SIMULACION: no se ha escrito nada. {total} filas se "
              f"actualizarian.")
        print("Para escribir:  --ejecutar")
    conn.close()


if __name__ == "__main__":
    main()
