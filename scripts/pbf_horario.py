"""Extraccion del PBF horario para la matriz maestra.

Sustituye al bloque `pdbc_*` del constructor v5, que queda RETIRADO por decision
del equipo (28-ago-2026). Tres motivos, los tres verificados contra la base:

  1. `esios_pdbc_gen` es una tabla DERIVADA (PBF menos bilaterales, ver
     `ingesta/refresh_pdbc.py`) y la derivacion convierte ceros en nulos. Medido:
     34.643 horas en las que `esios_pbf_gen.pumping_cons_mw = 0` y
     `esios_pdbc_gen.pumping_cons_mw IS NULL`. Ni un solo caso al reves.

  2. El constructor v5 agregaba el PDBC a MEDIA DIARIA (`pdbc_*_mean_lag1d`)
     porque reutilizaba el bloque de "contexto diario" del dataset diario -- ver
     el comentario de la linea 1740 del constructor. El PBF es horario y el
     target es horario: la media borra el perfil intradiario, que es justo donde
     esta la senal. Y ademas `groupby().mean()` ignoraba los NaN, asi que la
     "media diaria" del bombeo era la media de las horas CON bombeo: sesgo de
     2,4x respecto de la media real (-1.289 frente a -684 MW).

  3. El PBF no tiene ninguno de esos problemas: eje horario completo (58.391 de
     58.391 horas, cero huecos), ceros explicitos y una unica ausencia real.

FRONTERA DE INFORMACION -- lo importante para no meter fuga.
El PBF del dia X se publica a las 13:45 del dia X-1 (ver la cabecera de
`ingesta/esios_pbf_daily_pipeline.py`). Al predecir el precio de D+1 se decide a
las 12:00 del dia D, momento en el que:

    PBF del dia D    -> publicado a las 13:45 de D-1   DISPONIBLE
    PBF del dia D+1  -> se publica a las 13:45 de D    NO DISPONIBLE

Asi que el dato mas fresco utilizable es el PBF del propio dia D, en la misma
posicion que `es_esios_D` (el precio de D, tambien fijado antes del cierre). Por
eso el alineamiento por defecto es `_D` y no `_Dm1`: usar D-1 desperdicia un dia
de frescura sin ganar nada en seguridad. `alineamiento="Dm1"` esta disponible
para quien prefiera el criterio conservador.

Lo que NO se puede usar bajo ningun concepto es el PBF de D+1: sale de la misma
casacion que fija el precio de D+1, seria tan circular como usar el target.

Uso:
    python scripts/pbf_horario.py                  # escribe el parquet horario

    from pbf_horario import cargar_pbf, bloque_para_matriz
    pbf = cargar_pbf()                              # serie horaria cruda + limpia
    bloque = bloque_para_matriz(pbf)                # listo para unir por (fecha_pred, hora)
"""
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO / "ingesta"))
from config import load_config  # noqa: E402

import psycopg2  # noqa: E402

SALIDA = REPO / "data" / "bronze" / "pbf_horario.parquet"
TZ = "Europe/Madrid"

# Generacion programada por tecnologia. `total_gen_mw` se trae para poder cuadrar
# la suma; `unavailable_power_mw` es la indisponibilidad declarada en el propio
# programa -- misma frontera de informacion que el resto, no es dato de PBF
# posterior (aquella era `potencia_indisp_pbf_mw`, que el equipo ya retiro).
COLS_GEN = [
    "wind_mw", "solar_pv_mw", "solar_thermal_mw", "hydro_no_ugh_mw", "hydro_ugh_mw",
    "total_hydro_mw", "pumping_gen_mw", "pumping_cons_mw", "biomass_mw", "biogas_mw",
    "waste_mw", "other_renew_mw", "nuclear_mw", "ccgt_mw", "cogen_mw", "coal_mw",
    "fuel_gas_mw", "hybrid_mw", "total_gen_mw", "unavailable_power_mw",
]

# Demanda programada e interconexiones del mismo documento.
#
# `baleares_mw` NO se trae: el target es el precio del sistema PENINSULAR y el
# enlace balear no participa en su formacion. Mismo criterio con el que el equipo
# retiro `cap_baleares_prev_mw` del constructor (23-ago-2026).
#
# CUIDADO con `net_flow_ma_mw` (Marruecos) y `net_flow_ad_mw` (Andorra): su nulo
# NO es un cero. La cobertura de Marruecos crece de forma monotona -- 100 % de
# nulos en 2020, 98,6 % en 2021, 60,3 % en 2022, 45,8 % en 2023, 30,1 % en 2024,
# 15,5 % en 2025 y 11,6 % en 2026 -- porque la serie se fue publicando poco a
# poco, no porque no hubiera intercambio. Rellenar con 0 fabricaria un 2020 sin
# comercio con Marruecos, y ademas es justo el perfil de deriva train->test que
# el filtro de tendencias del constructor deberia tumbar. Se traen para que la
# depuracion decida con el dato delante, no se rellenan.
COLS_LOAD = [
    "demand_free_market_mw", "demand_reference_mw", "demand_direct_mw", "demand_aux_mw",
    "total_demand_mw", "net_flow_fr_mw", "net_flow_pt_mw", "net_flow_ma_mw",
    "net_flow_ad_mw", "total_net_flow_mw",
]

# Columnas cuyo NULL significa "esa tecnologia no programo en esa hora" y por
# tanto vale 0. Es la convencion de la fuente, no una imputacion: el propio
# pipeline de ingesta lo documenta ("las horas con menos de 4 muestras nativas
# no estan infravaloradas: son horas sin programa que ESIOS no publica").
# Medido sobre la tabla: pumping_cons tiene 34.732 ceros explicitos y 74 nulos,
# solar_thermal 1.719 nulos concentrados en inviernos de 2020 y 2022.
# NUCLEAR NO ENTRA AQUI, aunque sea una tecnologia de generacion: sus 82 nulos
# fuera del apagon caen todos entre el 1 y el 4 de mayo de 2025 (reposicion del
# sistema), el minimo publicado son 50 MW, el percentil 1 son 2.061 MW y no hay
# ni una sola hora a 0 en seis años. Un sistema sin nuclear no existe: ese nulo
# es falta de publicacion, no un cero.
NULO_ES_CERO = [
    "solar_pv_mw", "solar_thermal_mw", "pumping_gen_mw", "pumping_cons_mw",
    "biomass_mw", "biogas_mw", "waste_mw", "other_renew_mw", "hybrid_mw",
    "ccgt_mw", "coal_mw", "fuel_gas_mw", "cogen_mw",
]

# Ausencia REAL de publicacion: no se rellena con 0, porque no es que no hubiera
# programa, es que no hubo programa publicado. Son 37 horas y se explican solas:
#   28-abr-2025 de 12:00 a 23:00  (el apagon iberico corto a las 12:33)
#   29-abr-2025 completo          (reposicion del sistema)
#   26-oct-2025 hora 2            (cambio de hora)
# El resto de la ventana esta publicada sin un solo hueco.
#
# OJO: `pbf_publicado` marca la ausencia TOTAL de programa. La secuela del apagon
# es mas larga que eso -- la nuclear sigue sin publicarse del 1 al 4 de mayo --,
# que es justo la ventana 28-abr / 6-may que el constructor v5 excluia entera.
# `pbf_completo` marca esa segunda franja: hay programa, pero incompleto.
COL_TESTIGO_PUBLICACION = "wind_mw"
COL_TESTIGO_COMPLETITUD = "nuclear_mw"


def _conectar():
    _, db = load_config()
    return psycopg2.connect(**db)


def cargar_pbf(incluir_demanda: bool = True, verbose: bool = True) -> pd.DataFrame:
    """Trae el PBF horario ya limpio, con prefijo `pbf_` y eje temporal explicito."""
    con = _conectar()
    try:
        gen = pd.read_sql(
            f"SELECT datetime, {', '.join(COLS_GEN)} FROM esios_pbf_gen ORDER BY datetime", con)
        load = pd.read_sql(
            f"SELECT datetime, {', '.join(COLS_LOAD)} FROM esios_pbf_load_inter ORDER BY datetime",
            con) if incluir_demanda else None
    finally:
        con.close()

    df = gen if load is None else gen.merge(load, on="datetime", how="outer")
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)

    # Testigo de publicacion ANTES de rellenar nada: una vez puestos los ceros ya
    # no se distingue "no programo" de "no se publico".
    df["pbf_publicado"] = df[COL_TESTIGO_PUBLICACION].notna().astype(int)
    df["pbf_completo"] = (df["pbf_publicado"].astype(bool)
                          & df[COL_TESTIGO_COMPLETITUD].notna()).astype(int)
    sin_publicar = int((df["pbf_publicado"] == 0).sum())
    incompleto = int(((df["pbf_publicado"] == 1) & (df["pbf_completo"] == 0)).sum())

    rellenadas = {}
    for c in NULO_ES_CERO:
        if c not in df.columns:
            continue
        # Solo donde SI hubo publicacion. En las 37 horas del apagon se deja NaN.
        m = df[c].isna() & (df["pbf_publicado"] == 1)
        if m.any():
            rellenadas[c] = int(m.sum())
            df.loc[m, c] = 0.0

    df["ts_utc"] = df["datetime"]
    df["ts_local"] = df["datetime"].dt.tz_convert(TZ)
    df["fecha"] = df["ts_local"].dt.date
    df["hora"] = df["ts_local"].dt.hour
    df = df.drop(columns=["datetime"])

    medidas = [c for c in COLS_GEN + (COLS_LOAD if incluir_demanda else []) if c in df.columns]
    df = df.rename(columns={c: f"pbf_{c}" for c in medidas})
    orden = ["ts_utc", "ts_local", "fecha", "hora", "pbf_publicado", "pbf_completo"]
    df = df[orden + [f"pbf_{c}" for c in medidas]]

    if verbose:
        esperado = pd.date_range(df["ts_utc"].min(), df["ts_utc"].max(), freq="h", tz="UTC")
        print(f"PBF horario: {len(df):,} filas x {df.shape[1]} columnas")
        print(f"  rango           : {df['ts_local'].min()} -> {df['ts_local'].max()}")
        print(f"  eje horario     : {len(esperado):,} horas esperadas, "
              f"{len(esperado.difference(pd.DatetimeIndex(df['ts_utc']))):,} ausentes")
        print(f"  horas sin programa publicado (apagon + cambio de hora): {sin_publicar}")
        print(f"  horas con programa incompleto (secuela del apagon)     : {incompleto}")
        if rellenadas:
            print("  NULL -> 0 (convencion de la fuente, solo en horas publicadas):")
            for c, n in sorted(rellenadas.items(), key=lambda x: -x[1]):
                print(f"      pbf_{c:22s} {n:6,d} horas")
        resto = [c for c in df.columns if c.startswith("pbf_") and df[c].isna().any()]
        if resto:
            print("  nulos que se conservan (no son ceros de la fuente):")
            for c in resto:
                print(f"      {c:26s} {df[c].isna().sum():6,d} horas "
                      f"({df[c].isna().mean() * 100:.1f}%)")
    return df


def bloque_para_matriz(pbf: pd.DataFrame, alineamiento: str = "D") -> pd.DataFrame:
    """Bloque listo para unir a la matriz maestra por (`fecha_pred`, `hora`).

    `alineamiento="D"`   -> PBF del propio dia de prediccion D (publicado a las
                            13:45 de D-1, disponible al cerrar el mercado de D+1).
                            Sufijo `_D`, misma posicion que `es_esios_D`.
    `alineamiento="Dm1"` -> PBF del dia D-1. Sufijo `_Dm1`, criterio conservador.

    En los dos casos el emparejamiento es HORA A HORA: el valor de la hora h del
    dia de referencia va a la fila cuyo target es la hora h de D+1. No hay
    agregacion diaria de por medio -- ese era el defecto del bloque `pdbc_*`.
    """
    if alineamiento not in ("D", "Dm1"):
        raise ValueError("alineamiento debe ser 'D' o 'Dm1'")

    cols = [c for c in pbf.columns if c.startswith("pbf_")]
    cols = list(dict.fromkeys(cols))
    out = pbf[["ts_utc", "fecha", "hora"] + cols].copy()

    # En el retroceso horario de octubre la hora local 2 existe dos veces. La
    # matriz maestra indexa por (fecha, hora local), asi que hay que quedarse con
    # una: la primera en UTC, que es el mismo criterio que usa el constructor al
    # colapsar duplicados. Sin esto el merge INFLA la matriz (6 filas de mas).
    out = (out.sort_values("ts_utc")
              .drop_duplicates(subset=["fecha", "hora"], keep="first")
              .drop(columns=["ts_utc"]))

    # `fecha_pred` es el dia D de la matriz. Si el bloque debe llevar el PBF de
    # D-1, la fila de fecha F describe la prediccion hecha en F+1.
    desplazamiento = 0 if alineamiento == "D" else 1
    out["fecha_pred"] = pd.to_datetime(out["fecha"]) + pd.Timedelta(days=desplazamiento)

    sufijo = f"_{alineamiento}"
    out = out.rename(columns={c: c + sufijo for c in cols})
    return out.drop(columns=["fecha"])[
        ["fecha_pred", "hora"] + [c + sufijo for c in cols]
    ]


if __name__ == "__main__":
    pbf = cargar_pbf()
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    pbf.to_parquet(SALIDA, index=False)
    print(f"\nGuardado -> {SALIDA.relative_to(REPO)}")

    bloque = bloque_para_matriz(pbf)
    print(f"Bloque para la matriz (alineamiento D): {bloque.shape[0]:,} filas x "
          f"{bloque.shape[1]} columnas")
    print(f"  {[c for c in bloque.columns[:6]]} ...")
