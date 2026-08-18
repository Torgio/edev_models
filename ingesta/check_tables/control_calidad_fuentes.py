"""
TFM Energia UCM — Control de calidad entre fuentes duplicadas (ESIOS vs ENTSO-E)

Implementa la idea del punto #3 del Banco de Evidencias: la fuente que "pierde" en cada
duplicado no se descarta sin más -- se usa como control de calidad continuo sobre la fuente
"ganadora" (la que sí entra al modelo). Este script NUNCA escribe en la base de datos -- es de
solo lectura, y se ejecuta como paso de diagnostico ANTES del EDA/modelado, no como parte del
pipeline de ingesta. Piensa en el flujo asi:

    BD (solo lectura) -> este script -> reporte de divergencias -> EDA / dataset_diario

No decide nada por ti: informa qué días/variables se apartan mucho de lo normal, para que el
equipo decida qué hacer con ellos (investigar, descartar ese día puntual, o simplemente saberlo).

Metodología: para cada par (ganador, perdedor), se calcula la diferencia absoluta hora a hora
sobre TODO el histórico, y se usa el percentil 99 de esa serie como umbral -- así el umbral se
adapta a cada variable (nuclear y eólica no tienen la misma escala de ruido normal) en vez de
usar un número fijo arbitrario. Se listan los días con más horas por encima del umbral.

Uso:
    python check_tables/control_calidad_fuentes.py                  # todos los pares
    python check_tables/control_calidad_fuentes.py --par nuclear     # solo uno
    python check_tables/control_calidad_fuentes.py --guardar-csv     # guarda el detalle completo

NOTA (17-ago-2026): entsoe_load_inter esta en migracion en vivo (cambios de interconexiones) --
los pares que dependen de esa tabla (demanda, NTC) se dejan fuera hasta que se estabilice.
"""

import argparse
from pathlib import Path

import pandas as pd
import psycopg2

import sys
sys.path.append(str(Path(__file__).parent.parent))
from config import load_config

# (nombre, tabla_ganadora, columna_ganadora, tabla_perdedora, columna_perdedora, es_suma_de_dos)
# es_suma_de_dos: si la ganadora es la SUMA de dos columnas (caso solar+termosolar vs solar combinado ENTSO-E)
PARES = {
    "solar_total":  ("esios_gen", ["ree_gsolar_mw", "ree_gsolter_mw"], "entsoe_gen_data", ["solar_mw"]),
    "eolica":       ("entsoe_gen_data", ["wind_mw"], "esios_gen", ["ree_gwind_mw"]),
    "bombeo_gen":   ("entsoe_gen_data", ["pumping_gen_mw"], "esios_gen", ["ree_gpumping_mw"]),
    "bombeo_cons":  ("entsoe_gen_data", ["pumping_cons_mw"], "esios_gen", ["ree_cpumping_mw"]),
    "hidraulica":   ("entsoe_gen_data", ["hydro_run_river_mw", "hydro_reservoir_mw"], "esios_gen", ["ree_ghidro_mw"]),
    "nuclear":      ("entsoe_gen_data", ["nuclear_mw"], "esios_gen", ["ree_gnuclear_mw"]),
    # "demanda" y "ntc" pendientes -- entsoe_load_inter en migracion (ver nota arriba)
}

TABLA_TS = {"entsoe_gen_data": "datetime", "esios_gen": "datetime"}


def _cargar(conn, tabla: str, cols: list, ts_col: str) -> pd.DataFrame:
    df = pd.read_sql(f"SELECT {ts_col}, {', '.join(cols)} FROM {tabla} ORDER BY {ts_col}", conn)
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)   # normalizacion UTC manual, NUNCA parse_dates
    df["valor"] = df[cols].sum(axis=1)
    return df[[ts_col, "valor"]].rename(columns={ts_col: "ts"})


def evaluar_par(conn, nombre: str) -> pd.DataFrame:
    tabla_g, cols_g, tabla_p, cols_p = PARES[nombre]
    dg = _cargar(conn, tabla_g, cols_g, TABLA_TS[tabla_g]).rename(columns={"valor": "ganador"})
    dp = _cargar(conn, tabla_p, cols_p, TABLA_TS[tabla_p]).rename(columns={"valor": "perdedor"})

    comp = dg.merge(dp, on="ts", how="inner")
    comp["diff_abs"] = (comp["ganador"] - comp["perdedor"]).abs()
    comp["fecha"] = comp["ts"].dt.date

    umbral = comp["diff_abs"].quantile(0.99)
    comp["sospechoso"] = comp["diff_abs"] > umbral

    print(f"\n=== {nombre}  ({tabla_g}.{'+'.join(cols_g)}  vs  {tabla_p}.{'+'.join(cols_p)}) ===")
    print(f"horas comparadas: {len(comp):,}   umbral (p99 de la diferencia historica): {umbral:.1f} MW")
    print(f"horas sospechosas: {comp['sospechoso'].sum():,} ({comp['sospechoso'].mean()*100:.2f}%)")

    peores_dias = (
        comp[comp["sospechoso"]].groupby("fecha")
        .agg(horas_sospechosas=("sospechoso", "sum"), diff_media=("diff_abs", "mean"), diff_max=("diff_abs", "max"))
        .sort_values("horas_sospechosas", ascending=False).head(5)
    )
    if not peores_dias.empty:
        print("peores dias:")
        print(peores_dias.to_string())
    else:
        print("sin dias destacados por encima del umbral.")

    return comp


def main():
    parser = argparse.ArgumentParser(description="Control de calidad entre fuentes duplicadas ESIOS/ENTSO-E")
    parser.add_argument("--par", choices=list(PARES.keys()), default=None,
                        help="Evaluar solo un par (por defecto, todos)")
    parser.add_argument("--guardar-csv", action="store_true",
                        help="Guarda el detalle completo (todas las horas sospechosas) en control_calidad_detalle.csv")
    args = parser.parse_args()

    _, db_config = load_config()
    conn = psycopg2.connect(**db_config)

    pares_a_evaluar = [args.par] if args.par else list(PARES.keys())
    detalle_total = []

    for nombre in pares_a_evaluar:
        comp = evaluar_par(conn, nombre)
        if args.guardar_csv:
            sospechosos = comp[comp["sospechoso"]].copy()
            sospechosos["variable"] = nombre
            detalle_total.append(sospechosos)

    conn.close()

    if args.guardar_csv and detalle_total:
        out = pd.concat(detalle_total, ignore_index=True)
        out_path = Path(__file__).parent / "control_calidad_detalle.csv"
        out.to_csv(out_path, index=False)
        print(f"\nDetalle guardado en: {out_path}  ({len(out):,} filas)")


if __name__ == "__main__":
    main()
