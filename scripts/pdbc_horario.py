"""Bloque PDBC horario para la matriz maestra, leido de `esios_pdbc_gen`.

POR QUE PDBC Y NO PBF
El PBF (Programa Diario Base de Funcionamiento) incluye los contratos bilaterales, que son
energia comprometida FUERA del mercado. El PDBC es el PBF menos esos bilaterales, o sea el
resultado limpio de la casacion -- y es la casacion la que forma el precio. Para predecir
precio, el PDBC es la variable con sentido.

    PDBC = PBF - bilaterales nominados        (P.O. 3.1, apartado 4.1)

DE DONDE SALE EL DATO
De `esios_pdbc_gen`, la tabla que ya mantiene el equipo. No se recalcula: se lee. Verificado
columna a columna contra `esios_pbf_gen - COALESCE(bilateral, 0)` sobre las 58.391 horas del
historico, 15 de las 17 tecnologias salen IDENTICAS (diferencia maxima 0,0000).

LA EXCEPCION: el bombeo
Sus dos columnas llegan con los ceros convertidos en NULL. Ejemplo, madrugada del 10 de
junio de 2025:

    hora_local             esios_pbf_gen    esios_pdbc_gen
    2025-06-10 00:00                 0.0              None
    2025-06-10 01:00                 0.0              None

Son 34.643 horas en `pumping_cons_mw` y 20.690 en `pumping_gen_mw`. Y 0 no es lo mismo que
NULL: 0 dice "no hubo bombeo y lo sabemos", NULL dice "no sabemos". Tomarlas de la derivada
meteria 34.643 agujeros en la matriz que luego habria que imputar -- inventar un dato que
esta a un JOIN de distancia.

No es un fallo de la formula. El SQL de `refresh_pdbc.py` es correcto y pasa el bombeo sin
restar nada, porque no tiene contratos bilaterales que quitar:

    g.wind_mw - COALESCE(b.bil_wind_onshore_mw, 0),   <- las demas llevan su resta
    ...
    g.pumping_gen_mw, g.pumping_cons_mw               <- estas pasan tal cual

Lo que pasa es que ese refresco solo cubre `VENTANA_DIAS = 7`, y el historico anterior lo
escribio una version con el fallo. Se ve en la fecha de corte: cero celdas rotas dentro de
la ventana de 7 dias, y la ultima afectada es el 2026-08-16 con la tabla llegando al
2026-08-29.

Como para el bombeo PDBC y PBF son la misma cifra por definicion, aqui esas dos columnas se
leen de `esios_pbf_gen`. Mismo numero, con los ceros intactos. Ni se recalcula nada ni se
escribe en la base de datos.

Arreglar la tabla de verdad es un backfill de `refresh_pdbc.py` con la ventana abierta, y
eso es decision del equipo: es su tabla.

LAS TABLAS QUE COMPLETAN EL BLOQUE
    esios_pdbc_gen       casacion por tecnologia     ->  `pdbc_*`
    esios_pbf_gen        bombeo, total y no disponible, y el testigo de publicacion
    esios_pbf_load_inter demanda e interconexiones   ->  `pbfli_*`
    esios_pbf_bilateral  intermediacion              ->  `bil_*`

Los bilaterales POR TECNOLOGIA no entran como features: ya estan consumidos en la resta que
define el PDBC, y volver a meterlos crearia colinealidad exacta. Si entran los de
INTERMEDIACION -- volumen total, comercializadoras, consumidores directos --, que no son
generacion y miden cuanta energia esquiva el mercado.

CONVENCION NULL/0. Las tablas usan NULL para "no hubo": `bil_coal_mw` es 91 % nulos y cero
ceros explicitos. En las tecnologias, un NULL en hora publicada significa "no programo", que
es 0. Solo se rellena donde hubo publicacion: durante el apagon las columnas llegan como
NaN y ahi el cero seria falso.

FRONTERA DE INFORMACION. El PBF del dia X se publica a las 13:45 de X-1, o sea DESPUES del
cierre del mercado (12:00). Al predecir D+1 a las 12:00 de D, el mas fresco disponible es
el del propio dia D. De ahi el sufijo `_D`. El de D+1 seria fuga: sale de la misma casacion
que fija el precio que se quiere predecir.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))

TZ = "Europe/Madrid"
SUFIJO = "_D"

# Las 17 tecnologias de `esios_pdbc_gen`, en su orden.
TECNOLOGIAS = ["wind_mw", "solar_pv_mw", "solar_thermal_mw", "hydro_ugh_mw",
               "hydro_no_ugh_mw", "nuclear_mw", "coal_mw", "cogen_mw", "biomass_mw",
               "biogas_mw", "hybrid_mw", "ccgt_mw", "fuel_gas_mw", "waste_mw",
               "other_renew_mw", "pumping_gen_mw", "pumping_cons_mw"]

# Las dos que se leen de `esios_pbf_gen` en lugar de la derivada. Ver la nota de arriba:
# misma cifra por definicion, pero con los ceros intactos.
DESDE_PBF = ["pumping_gen_mw", "pumping_cons_mw"]

# Solo estan en `esios_pbf_gen`; la derivada no las lleva.
EXTRA_GEN = ["total_gen_mw", "unavailable_power_mw"]

# Demanda e interconexiones. `baleares_mw` fuera: el target es el sistema peninsular y el
# enlace balear no participa en su formacion (mismo criterio con el que el equipo retiro
# `cap_baleares_prev_mw`).
COLS_LOAD = ["demand_free_market_mw", "demand_reference_mw", "demand_direct_mw",
             "demand_aux_mw", "total_demand_mw", "net_flow_fr_mw", "net_flow_pt_mw",
             "net_flow_ma_mw", "net_flow_ad_mw", "total_net_flow_mw"]

# Bilaterales de INTERMEDIACION: no son generacion, asi que no se restan a nadie y entran
# como features propias. Miden el volumen que se contrata fuera del mercado.
COLS_BILATERAL = ["bil_total_sales_mw", "bil_total_purchases_mw",
                  "bil_retail_free_sales_mw", "bil_retail_free_buy_mw",
                  "bil_retail_last_resort_mw", "bil_direct_consumer_mw",
                  "bil_generic_sales_mw", "bil_generic_buy_mw"]

# La regla NULL -> 0 NO se aplica a estas. La nuclear nunca marca cero: su minimo en seis
# anos son 50 MW, el percentil 1 son 2.061 y no hay ni una hora a 0. Sus nulos (82, del 1
# al 4 de mayo de 2025) son secuela del apagon -- falta de publicacion, no parada total --
# y ponerlos a cero inventaria un sistema sin nuclear.
NO_NULO_ES_CERO = ("nuclear_mw",)

COL_TESTIGO = "wind_mw"


def _conexion():
    from config import load_config
    import psycopg2
    _, db = load_config()
    return psycopg2.connect(**db)


def cargar(verbose: bool = True) -> pd.DataFrame:
    """PDBC horario de `esios_pdbc_gen`, mas demanda/flujos y bilaterales de intermediacion."""
    de_tabla = [c for c in TECNOLOGIAS if c not in DESDE_PBF]
    cols_gen = DESDE_PBF + EXTRA_GEN + [COL_TESTIGO]

    con = _conexion()
    try:
        p = pd.read_sql(f"SELECT datetime, {', '.join(de_tabla)} FROM esios_pdbc_gen", con)
        g = pd.read_sql(f"SELECT datetime, {', '.join(cols_gen)} FROM esios_pbf_gen", con)
        b = pd.read_sql(f"SELECT datetime, {', '.join(COLS_BILATERAL)} FROM esios_pbf_bilateral", con)
        l = pd.read_sql(f"SELECT datetime, {', '.join(COLS_LOAD)} FROM esios_pbf_load_inter", con)
    finally:
        con.close()

    for d in (p, g, b, l):
        d["datetime"] = pd.to_datetime(d["datetime"], utc=True)

    # El testigo sale del PBF y es homonimo de una columna del PDBC: se renombra para que
    # el merge no lo desdoble en `wind_mw_x` / `wind_mw_y`.
    g = g.rename(columns={COL_TESTIGO: "_testigo"})
    df = (p.merge(g, on="datetime", how="left")
           .merge(b, on="datetime", how="left")
           .merge(l, on="datetime", how="left")
           .sort_values("datetime").reset_index(drop=True))

    # Testigos ANTES de rellenar: despues ya no se distingue "no hubo" de "no se publico".
    df["pbf_publicado"] = df["_testigo"].notna().astype(int)
    df["pbf_completo"] = (df["pbf_publicado"].astype(bool)
                          & df["nuclear_mw"].notna()).astype(int)
    publicado = df["pbf_publicado"] == 1

    # NULL de tecnologia = no programo = 0, pero solo donde hubo publicacion y nunca en
    # las de NO_NULO_ES_CERO.
    rellenadas = {}
    for tec in TECNOLOGIAS:
        m = df[tec].isna() & publicado & (tec not in NO_NULO_ES_CERO)
        if m.any():
            rellenadas[tec] = int(m.sum())
            df[tec] = df[tec].mask(m, 0.0)

    df = df.rename(columns={c: f"pdbc_{c}" for c in TECNOLOGIAS + EXTRA_GEN})
    df = df.rename(columns={c: f"pbfli_{c}" for c in COLS_LOAD})

    df["ts_utc"] = df["datetime"]
    df["fecha"] = df["datetime"].dt.tz_convert(TZ).dt.date
    df["hora"] = df["datetime"].dt.tz_convert(TZ).dt.hour

    salida = (["ts_utc", "fecha", "hora", "pbf_publicado", "pbf_completo"]
              + [f"pdbc_{c}" for c in TECNOLOGIAS + EXTRA_GEN]
              + [f"pbfli_{c}" for c in COLS_LOAD]
              + [c for c in COLS_BILATERAL if c in df.columns])
    out = df[salida]

    if verbose:
        print(f"PDBC horario: {len(out):,} filas x {out.shape[1]} columnas")
        print(f"  {len(de_tabla)} tecnologias leidas de esios_pdbc_gen")
        print(f"  {len(DESDE_PBF)} de esios_pbf_gen (ceros intactos): {', '.join(DESDE_PBF)}")
        print(f"  horas sin programa publicado: {int((~publicado).sum())}")
        if rellenadas:
            print("  NULL -> 0 (convencion de la fuente, solo en horas publicadas):")
            for c, n in sorted(rellenadas.items(), key=lambda x: -x[1])[:6]:
                print(f"      {c:20s} {n:6,d}")
    return out


def bloque_para_matriz(pdbc: pd.DataFrame, alineamiento: str = "D") -> pd.DataFrame:
    """Listo para unir por (`fecha_pred`, `hora`). Ver la nota de frontera de informacion."""
    if alineamiento not in ("D", "Dm1"):
        raise ValueError("alineamiento debe ser 'D' o 'Dm1'")
    cols = [c for c in pdbc.columns if c not in ("ts_utc", "fecha", "hora")]
    out = pdbc[["ts_utc", "fecha", "hora"] + cols].copy()

    # El retroceso horario de octubre repite la hora local 2; la matriz indexa por
    # (fecha, hora), asi que se colapsa quedandose con la primera en UTC.
    out = (out.sort_values("ts_utc")
              .drop_duplicates(subset=["fecha", "hora"], keep="first")
              .drop(columns=["ts_utc"]))

    out["fecha_pred"] = (pd.to_datetime(out["fecha"])
                         + pd.Timedelta(days=0 if alineamiento == "D" else 1))
    suf = f"_{alineamiento}"
    out = out.rename(columns={c: c + suf for c in cols})
    return out[["fecha_pred", "hora"] + [c + suf for c in cols]]


if __name__ == "__main__":
    d = cargar()
    b = bloque_para_matriz(d)
    print(f"\nBloque: {b.shape[0]:,} filas x {b.shape[1]} columnas")
    for pre in ("pdbc_", "pbfli_", "bil_"):
        print(f"  {pre:7s}: {sum(c.startswith(pre) for c in b.columns)}")
