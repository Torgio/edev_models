"""Analisis exploratorio y depuracion de variables del bronce unificado.

Sigue la metodologia del modulo 06.1 del master (`Tarea_Eleccionesy_v2.py`), con
sus mismas diez secciones y las mismas funciones de clase
(`scripts/funciones_mineria.py`), adaptada a serie horaria en `scripts/depuracion.py`.

Objetivo: llegar a una lista razonada de variables candidatas ANTES de fijar la
matriz final de modelado, con la evidencia de cada descarte por escrito.

Uso:
    python scripts/depuracion_variables.py

Salidas:
    data/bronze/bronze_depurado.parquet   dataset depurado
    docs/depuracion_variables.xlsx        descriptivos, atipicos, redundancia, ranking
    docs/figuras/*.png                    patron de perdidos, V de Cramer, correlaciones
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # sin display: el script corre en terminal, no en notebook
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT / "scripts"))

from bronze_config import BRONZE_DIR, UNIFIED_FILENAME  # noqa: E402
import depuracion as dep  # noqa: E402
from funciones_mineria import graficoVcramer, patron_perdidos  # noqa: E402

FIG_DIR = REPO_ROOT / "docs" / "figuras"
FIG_DIR.mkdir(parents=True, exist_ok=True)
SALIDA_PARQUET = BRONZE_DIR / "bronze_depurado.parquet"
SALIDA_EXCEL = REPO_ROOT / "docs" / "depuracion_variables.xlsx"

TARGET = "spot_es_esios"

# Excluidas del pool de candidatas por decision ya cerrada en docs/decisiones_datos.md.
# Se conservan en el parquet -- son la evidencia de esas decisiones -- pero no
# compiten por entrar en la matriz final.
EXCLUIDAS_POR_DECISION = {
    "entsoe_gas_mw": "D-02: mezcla CCGT + cogeneracion; se usan ree_gccgas + ree_gotherthermal por separado",
    "esios_gen_ree_gsolar_mw": "D-03: contaminada con autoconsumo; se usa calc_solar_fv_mw",
    "load_inter_ree_load": "D-03: incluye autoconsumo; se usa load_inter_entsoe_load",
}

# Precios de otras zonas: son mercados acoplados, no predictores disponibles a D-1
# a la hora de cerrar el diario espanol. Se separan del pool para no confundir
# correlacion alta con utilidad predictiva.
def es_precio_otra_zona(col):
    return col.startswith("spot_") and col != TARGET


def titulo(n, texto):
    print("\n" + "=" * 78)
    print(f"{n}. {texto}")
    print("=" * 78)


def guardar_fig(nombre):
    ruta = FIG_DIR / nombre
    plt.tight_layout()
    plt.savefig(ruta, dpi=110, bbox_inches="tight")
    plt.close("all")
    print(f"    figura -> {ruta.relative_to(REPO_ROOT)}")


informes = {}  # hoja de excel -> dataframe


# ============================================================================
# 0. CARGA
# ============================================================================
titulo(0, "CARGA DEL BRONCE UNIFICADO")

datos = pd.read_parquet(BRONZE_DIR / UNIFIED_FILENAME)
print(f"Filas: {len(datos):,}   Columnas: {datos.shape[1]}")
print(f"Rango: {datos['ts_utc'].min()} -> {datos['ts_utc'].max()}")


# ============================================================================
# 1. OBJETIVOS
# ============================================================================
titulo(1, "OBJETIVOS")
print("""
Variable objetivo: spot_es_esios (precio horario del mercado diario espanol).

Este script NO modela. Su unico producto es una lista de variables candidatas
con la evidencia de por que cada una entra o sale. La seleccion final de la
matriz se decide sobre esa lista, no sobre el bronce crudo.

Nota de alcance: el ranking de la seccion 10 mide asociacion CONTEMPORANEA
(variable en la hora h frente a precio en la hora h). Mide relacion fisica, no
disponibilidad a D-1. La comprobacion de fuga de informacion es un paso
independiente y ya vive en modelos/construir_dataset_maestro.py.
""")


# ============================================================================
# 2. TIPADO DE VARIABLES
# ============================================================================
titulo(2, "TIPADO DE VARIABLES")

numericas = [
    c for c in datos.select_dtypes(include=["int", "int32", "int64", "float", "float32", "float64"]).columns
    if c not in dep.COLS_CALENDARIO
]
categoricas = [c for c in datos.columns if c not in numericas and c not in dep.COLS_CALENDARIO]

print(f"Numericas : {len(numericas)}")
print(f"Categoricas: {len(categoricas)}  {categoricas}")
print(f"Calendario : {len(dep.COLS_CALENDARIO)}  (no son variables de analisis)")

granos = dep.mapa_granos()
resumen_grano = pd.Series([granos.get(c, "derivada") for c in numericas]).value_counts()
print("\nGrano de origen de las numericas:")
print(resumen_grano.to_string())


# ============================================================================
# 3. ANALISIS DESCRIPTIVO (sobre el bronce crudo)
# ============================================================================
titulo(3, "ANALISIS DESCRIPTIVO")

desc_crudo = dep.descriptivo_numericas(datos, numericas)
informes["3_descriptivo_crudo"] = desc_crudo

print("\n--- Variables con mas del 50% de nulos en el bronce crudo ---")
altos = desc_crudo[desc_crudo["%_Missing"] > 50].sort_values("%_Missing", ascending=False)
for v, r in altos.iterrows():
    print(f"  {v:36s} {r['%_Missing']:5.1f}%   grano={granos.get(v, 'derivada')}")
print(f"\n  Total: {len(altos)} variables.")
print("  ATENCION: la regla de clase ('eliminar si >50% missing') borraria aqui")
print("  gas, CO2, meteo y toda la potencia instalada. Son nulos de GRANO, no")
print("  perdidos. Se reconstruye el grano en 4.1 y se vuelve a medir.")

print("\n--- Distribucion: las 10 mas asimetricas ---")
print(desc_crudo.sort_values("Asimetria", key=np.abs, ascending=False)[
    ["mean", "50%", "max", "Asimetria", "Kurtosis"]].head(10).to_string())


# ============================================================================
# 4. CORRECCION DE ERRORES DETECTADOS
# ============================================================================
titulo(4, "CORRECCION DE ERRORES DETECTADOS")
correcciones = []

# --- 4.1 Reconstruccion del grano -------------------------------------------
print("\n--- 4.1 Reconstruccion del grano ---")
datos, informe_grano = dep.reconstruir_grano(datos)
informes["4.1_grano"] = informe_grano
print(f"  Reconstruidas {len(informe_grano)} columnas (diarias difundidas a 24h,")
print(f"  ERA5 interpolado a lo sumo 2 horas entre pasos de 3h).")
print(informe_grano.head(8).to_string(index=False))
correcciones.append(("4.1", "grano", f"{len(informe_grano)} columnas", "difusion diaria + interpolacion 3h"))

# --- 4.2 Commodities: el mercado cierra los fines de semana ------------------
print("\n--- 4.2 Commodities: arrastre de fin de semana ---")
com_cierre = ["commodities_co2_eua_dec", "commodities_gas_ttf_m1"]
com_cierre = [c for c in com_cierre if c in datos.columns]
for c in com_cierre:
    antes = datos[c].isna().mean() * 100
    # El precio de cierre del viernes es el precio vigente hasta el lunes: no es
    # imputacion, es como funciona el mercado. Limite 4 dias cubre puentes largos.
    datos[c] = datos[c].ffill(limit=24 * 4)
    print(f"  {c:28s} nulos {antes:5.1f}% -> {datos[c].isna().mean() * 100:4.1f}%  (cierre del ultimo dia habil)")
    correcciones.append(("4.2", c, f"{antes:.1f}% -> {datos[c].isna().mean() * 100:.1f}%", "arrastre de cierre"))
print("  MIBGAS no entra aqui: cotiza los siete dias, no tiene huecos de calendario.")

# --- 4.3 Columnas constantes -------------------------------------------------
print("\n--- 4.3 Columnas constantes o casi constantes ---")
constantes = [c for c in numericas if datos[c].dropna().nunique() <= 2]
for c in constantes:
    vals = sorted(datos[c].dropna().unique())
    print(f"  {c:36s} distintos={len(vals)}  {vals}")
print(f"\n  {len(constantes)} variables sin varianza util -> se eliminan del pool.")
correcciones.append(("4.3", "constantes", f"{len(constantes)} variables", "eliminadas del pool"))

# --- 4.4 Convencion NULL/0 en bombeo -----------------------------------------
print("\n--- 4.4 Convencion NULL/0 en bombeo ---")
col_pg = "entsoe_pumping_gen_mw"
inicio_pg = datos.loc[datos[col_pg].notna(), "ts_utc"].min()
mask_post = datos["ts_utc"] >= inicio_pg
n_null_post = datos.loc[mask_post, col_pg].isna().sum()
print(f"  {col_pg}: primer dato {inicio_pg}")
print(f"  Nulos anteriores a esa fecha : {datos.loc[~mask_post, col_pg].isna().sum():,}  (ausencia real de serie)")
print(f"  Nulos posteriores            : {n_null_post:,}  (horas sin bombeo, codificadas NULL)")
print(f"  Comparacion: entsoe_pumping_cons_mw codifica esas mismas horas como 0")
print(f"  ({(datos['entsoe_pumping_cons_mw'] == 0).mean() * 100:.1f}% de ceros). Convencion asimetrica.")
datos.loc[mask_post, col_pg] = datos.loc[mask_post, col_pg].fillna(0)
print(f"  -> NULL = 0 solo dentro de la serie ({n_null_post:,} celdas). Antes se deja NaN.")
correcciones.append(("4.4", col_pg, f"{n_null_post} celdas", "NULL->0 dentro de la serie"))

# --- 4.5 Negativos fisicamente imposibles ------------------------------------
print("\n--- 4.5 Generacion negativa ---")
for c in ["esios_gen_ree_gsolter_mw", "esios_gen_ree_gsolar_mw", "calc_solar_fv_mw"]:
    if c not in datos.columns:
        continue
    n = int((datos[c] < 0).sum())
    if n:
        print(f"  {c:30s} {n:5d} valores negativos (min {datos[c].min():.1f}) -> a 0")
        datos[c] = datos[c].clip(lower=0)
        correcciones.append(("4.5", c, f"{n} valores", "clip a 0"))
print("  NO se tocan: netflow_* y total_net_flow_mw (el signo es la direccion del")
print("  flujo), ghidro (incluye consumo de bombeo, ver E.2), cbattery (carga),")
print("  ni demanda_residual_prev (excedente renovable real).")

# --- 4.6 Ceros espurios en demanda -------------------------------------------
print("\n--- 4.6 Ceros espurios en demanda ---")
col_load = "load_inter_entsoe_load"
n_cero = int((datos[col_load] == 0).sum())
print(f"  {col_load}: {n_cero} horas a exactamente 0 MW. Una demanda peninsular")
print("  nula es imposible -> fallo de publicacion. Se pasan a NaN y se interpolan en 8.")
datos.loc[datos[col_load] == 0, col_load] = np.nan
correcciones.append(("4.6", col_load, f"{n_cero} horas", "cero -> NaN"))

# --- 4.7 Series con arranque tardio ------------------------------------------
print("\n--- 4.7 Series que no cubren toda la ventana ---")
filas_cob = []
for c in numericas:
    s = datos.loc[datos[c].notna(), "ts_utc"]
    if s.empty:
        continue
    cobertura = datos[c].notna().mean()
    if cobertura < 0.98:
        filas_cob.append((c, s.min(), s.max(), cobertura * 100))
cobertura_df = pd.DataFrame(filas_cob, columns=["variable", "desde", "hasta", "pct_cobertura"]).sort_values(
    "pct_cobertura"
)
informes["4.7_cobertura"] = cobertura_df
print(cobertura_df.to_string(index=False))
print("\n  No se eliminan por regla automatica: una serie que arranca en 2024 puede")
print("  ser la mejor variable del modelo si se entrena desde 2024. La decision")
print("  depende de la ventana de entrenamiento, no del porcentaje de nulos.")

# --- 4.8 Redundancia ya decidida ---------------------------------------------
print("\n--- 4.8 Variables excluidas por decision documentada ---")
for c, motivo in EXCLUIDAS_POR_DECISION.items():
    print(f"  {c:30s} {motivo}")

informes["4_correcciones"] = pd.DataFrame(
    correcciones, columns=["seccion", "variable", "alcance", "accion"]
)


# ============================================================================
# 5. SEPARACION OBJETIVO / INPUTS
# ============================================================================
titulo(5, "SEPARACION OBJETIVO / INPUTS")

var_objetivo = datos[TARGET].copy()
print(f"Objetivo: {TARGET}")
print(var_objetivo.describe().to_string())
print(f"\nNulos en el objetivo: {var_objetivo.isna().sum()}")

precios_otras_zonas = [c for c in numericas if es_precio_otra_zona(c)]
variables_input = [
    c for c in numericas
    if c != TARGET
    and c not in constantes
    and c not in EXCLUIDAS_POR_DECISION
    and c not in precios_otras_zonas
]
print(f"\nPool de candidatas: {len(variables_input)} variables")
print(f"  descartadas por constantes        : {len(constantes)}")
print(f"  descartadas por decision cerrada  : {len(EXCLUIDAS_POR_DECISION)}")
print(f"  apartadas (precios de otras zonas): {len(precios_otras_zonas)}")


# ============================================================================
# 6. ANALISIS DE VALORES ATIPICOS (diagnostico, no tratamiento)
# ============================================================================
titulo(6, "ANALISIS DE VALORES ATIPICOS")
print("""
Criterio de clase (atipicosAmissing): un valor es atipico solo si cumple A LA VEZ
  - |z| > 3 si la variable es simetrica, o |MAD-score| > 8 si es asimetrica, y
  - queda fuera de Q1 - 3*IQR / Q3 + 3*IQR.

DECISION, y es distinta a la de la tarea de clase: aqui NO se convierten a
missing. En una serie de precio horario el extremo es la senal -- una hora a
300 EUR/MWh en una ola de calor sin viento es justo lo que hay que predecir, y
sustituirla por la mediana destruiria el fenomeno. El criterio se usa para
ordenar variables por rareza y decidir caso a caso.
""")

atipicos = dep.diagnostico_atipicos(datos, variables_input + [TARGET])
informes["6_atipicos"] = atipicos
print(atipicos.head(15).to_string(index=False))
print(f"\nVariables con algun atipico: {(atipicos['n_atipicos'] > 0).sum()} de {len(atipicos)}")

plt.figure(figsize=(9, 7))
top = atipicos[atipicos["n_atipicos"] > 0].head(20).iloc[::-1]
plt.barh(top["variable"], top["pct_atipicos"], color="steelblue")
plt.xlabel("% de valores marcados como atipicos (criterio 06.1)")
plt.title("Diagnostico de atipicos -- no se corrigen, se ordenan")
guardar_fig("06_atipicos.png")


# ============================================================================
# 7. ANALISIS DE VALORES PERDIDOS
# ============================================================================
titulo(7, "ANALISIS DE VALORES PERDIDOS")

prop_var = datos[variables_input].isna().mean()
con_nulos = prop_var[prop_var > 0].sort_values(ascending=False)
print(f"\nVariables con algun perdido: {len(con_nulos)} de {len(variables_input)}")
print(con_nulos.head(20).apply(lambda x: f"{x * 100:.2f}%").to_string())

datos["prop_missings"] = datos[variables_input].isna().mean(axis=1)
print("\n--- prop_missings por observacion ---")
print(datos["prop_missings"].describe().to_string())
n_malas = int((datos["prop_missings"] > 0.5).sum())
print(f"\nObservaciones con >50% de campos perdidos: {n_malas}")
if n_malas:
    rango_malas = datos.loc[datos["prop_missings"] > 0.5, "ts_utc"]
    print(f"  Concentradas entre {rango_malas.min()} y {rango_malas.max()}")
print("  No se eliminan filas: en una serie temporal un hueco es informacion")
print("  (y borrar la fila rompe la continuidad del eje). Se marca y punto.")

cols_con_nulos = list(con_nulos.index)
if len(cols_con_nulos) > 1:
    plt.figure(figsize=(11, 9))
    corr_na = datos[cols_con_nulos].isna().corr()
    sns.heatmap(corr_na, cmap="coolwarm", center=0, mask=np.triu(np.ones_like(corr_na, dtype=bool)),
                cbar_kws={"shrink": 0.6})
    plt.title("Patron de perdidos -- correlacion entre ausencias")
    guardar_fig("07_patron_perdidos.png")

informes["7_perdidos"] = con_nulos.rename("prop_nulos").reset_index().rename(columns={"index": "variable"})


# ============================================================================
# 8. IMPUTACION
# ============================================================================
titulo(8, "IMPUTACION")
print("""
Se sustituye la mediana global de la metodologia de clase por interpolacion
temporal acotada a 3 horas. Motivo: la mediana de la demanda peninsular es un
valor de tarde plantado a las 4 de la manana. La interpolacion respeta la forma
local; los huecos de mas de 3 horas se dejan en NaN, declarados.
""")

datos, informe_imput = dep.imputacion_temporal(datos, variables_input, limite=3)
informes["8_imputacion"] = informe_imput.reset_index().rename(columns={"index": "variable"})
print(informe_imput.head(20).to_string())
restantes = datos[variables_input].isna().sum().sum()
print(f"\nCeldas perdidas restantes: {restantes:,} "
      f"({restantes / (len(datos) * len(variables_input)) * 100:.2f}% del pool)")


# ============================================================================
# 9. REDUNDANCIA ENTRE CANDIDATAS
# ============================================================================
titulo(9, "REDUNDANCIA ENTRE CANDIDATAS")

redundancia = dep.bloques_correlacion(datos, variables_input, umbral=0.95)
informes["9_redundancia"] = redundancia
print(f"Pares con |r| >= 0.95: {len(redundancia)}")
print(redundancia.head(25).to_string(index=False))
print("\n  Cada par es una eleccion pendiente: entran las dos o entra una. La")
print("  decision no es estadistica, es de fuente (cual se publica antes y mejor).")

num_para_corr = [c for c in variables_input if datos[c].notna().mean() > 0.9][:40]
plt.figure(figsize=(13, 11))
sns.heatmap(datos[num_para_corr].corr(), cmap="coolwarm", center=0, vmin=-1, vmax=1,
            cbar_kws={"shrink": 0.6})
plt.title("Matriz de correlacion (candidatas con cobertura > 90%)")
guardar_fig("09_matriz_correlacion.png")


# ============================================================================
# 10. RANKING DE CANDIDATAS FRENTE AL OBJETIVO
# ============================================================================
titulo(10, "RANKING DE CANDIDATAS FRENTE AL OBJETIVO")

ranking = dep.ranking_asociacion(datos, variables_input, TARGET)
informes["10_ranking"] = ranking
print(ranking.head(30).to_string(index=False))

print("\n--- Cola: candidatas con asociacion despreciable ---")
cola = ranking[(ranking["v_cramer"] < 0.10) & (ranking["abs_pearson"] < 0.10)]
print(cola[["variable", "pearson", "spearman", "v_cramer"]].to_string(index=False))
print(f"\n  {len(cola)} variables sin senal contemporanea sobre el precio.")

plt.figure(figsize=(9, 10))
top = ranking.dropna(subset=["v_cramer"]).head(30).iloc[::-1]
plt.barh(top["variable"], top["v_cramer"], color="skyblue")
plt.xlabel("V de Cramer frente a spot_es_esios")
plt.title("Importancia de variables (criterio 06.1)")
guardar_fig("10_vcramer.png")

plt.figure(figsize=(9, 10))
top_p = ranking.dropna(subset=["abs_pearson"]).sort_values("abs_pearson", ascending=False).head(30).iloc[::-1]
colores = ["indianred" if v < 0 else "seagreen" for v in top_p["pearson"]]
plt.barh(top_p["variable"], top_p["pearson"], color=colores)
plt.axvline(0, color="black", linewidth=0.8)
plt.xlabel("Correlacion de Pearson con spot_es_esios")
plt.title("Relacion lineal con el precio (verde: directa, rojo: inversa)")
guardar_fig("10_pearson.png")


# ============================================================================
# 11. GUARDADO
# ============================================================================
titulo(11, "GUARDADO")

cols_finales = dep.COLS_CALENDARIO + [TARGET] + variables_input + ["prop_missings"]
cols_finales += [c for c in datos.columns if c not in cols_finales]  # se conserva todo
datos[cols_finales].to_parquet(SALIDA_PARQUET, index=False)
print(f"Parquet depurado -> {SALIDA_PARQUET.relative_to(REPO_ROOT)}  ({datos.shape[0]:,} x {len(cols_finales)})")

def sin_zona_horaria(tabla):
    """Excel no admite datetimes con tz -- se pasan a texto ISO antes de volcar."""
    t = tabla.copy()
    for c in t.columns:
        if isinstance(t[c].dtype, pd.DatetimeTZDtype):
            t[c] = t[c].dt.tz_convert("UTC").dt.strftime("%Y-%m-%d %H:%M")
    return t


with pd.ExcelWriter(SALIDA_EXCEL, engine="openpyxl") as xl:
    for hoja, tabla in informes.items():
        sin_zona_horaria(tabla).to_excel(xl, sheet_name=hoja[:31])
print(f"Informe          -> {SALIDA_EXCEL.relative_to(REPO_ROOT)}  ({len(informes)} hojas)")

print("\n" + "=" * 78)
print("RESUMEN")
print("=" * 78)
print(f"  Numericas en el bronce      : {len(numericas)}")
print(f"  - constantes                : {len(constantes)}")
print(f"  - excluidas por decision    : {len(EXCLUIDAS_POR_DECISION)}")
print(f"  - precios de otras zonas    : {len(precios_otras_zonas)} (apartados, no descartados)")
print(f"  = pool de candidatas        : {len(variables_input)}")
print(f"  - sin senal contemporanea   : {len(cola)}")
print(f"  = candidatas con senal      : {len(variables_input) - len(cola)}")
print(f"\n  Pares redundantes |r|>=0.95 : {len(redundancia)} (pendientes de arbitrar)")
