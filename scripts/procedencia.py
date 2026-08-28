"""Catalogo de procedencia: de que tabla de Postgres sale cada columna de la matriz.

El constructor v5 ya llevaba uno (`ORIGEN_COLUMNAS` / `_origen`, usado para colorear
el Excel documentado). Se copia aqui con sus mismas reglas -- no se reinventa -- y se
extiende con lo que aquel no conocia:

  - el bloque `pbf_*`, que sustituye al `pdbc_*` retirado, separando las columnas que
    vienen de `esios_pbf_gen` de las de `esios_pbf_load_inter`;
  - las columnas que nacen en los notebooks de depuracion (banderas del apagon,
    testigos de imputacion), marcadas como DERIVADA para que nadie las busque en la BD.

Reglas de casacion, identicas a las del constructor y en este orden:
    "_xxx"  -> casa por SUFIJO
    "xxx_"  -> casa por PREFIJO
    "xxx*"  -> casa por PREFIJO (para prefijos sin guion bajo final, como "ree_g")
    "xxx"   -> coincidencia EXACTA
El ORDEN de la lista importa: "ree_ntc_" y "ree_load_" van antes que "ree_g*" para que
no se las quede el bloque equivocado.

Uso:
    from procedencia import origen, con_origen, resumen_origen

    origen("pbf_coal_mw_D")            -> 'esios_pbf_gen'
    con_origen(tabla, "variable")      -> la misma tabla con una columna `tabla_origen`
    resumen_origen(lista_de_columnas)  -> recuento por tabla de origen
"""
import pandas as pd

# Columnas de `esios_pbf_gen` frente a las de `esios_pbf_load_inter`. Ambas llegan con
# prefijo `pbf_` y sufijo de alineamiento (`_D` / `_Dm1`), asi que el prefijo por si solo
# no distingue: hay que mirar el nombre de la medida.
_PBF_LOAD = (
    "demand_free_market", "demand_reference", "demand_direct", "demand_aux",
    "total_demand", "net_flow_fr", "net_flow_pt", "net_flow_ma", "net_flow_ad",
    "total_net_flow", "baleares",
)

# (claves, tabla de origen). Copiado de ORIGEN_COLUMNAS del constructor v5 y ampliado.
ORIGEN_COLUMNAS = [
    (("fecha_pred", "fecha_objetivo", "hora", "ts", "split"), "CLAVE"),

    # --- nacidas en los notebooks de depuracion -------------------------------
    (("dia_apagon", "ventana_pisa_apagon", "target_contrafactual",
      "prop_missings"), "DERIVADA (depuracion)"),
    (("_era_nulo",), "DERIVADA (testigo de imputacion)"),
    # Con `*` para que casen por prefijo: en la matriz llevan sufijo de alineamiento
    # (`pbf_publicado_D`) y una coincidencia exacta no los alcanzaria.
    (("pbf_publicado*", "pbf_completo*"), "DERIVADA (testigo de publicacion)"),

    # --- precio ---------------------------------------------------------------
    (("target_price",), "spot_price (TARGET)"),
    (("es_esios_", "spread_es_"), "spot_price (lag)"),
    (("pt_entsoe_D", "fr_entsoe_D", "de_lu_entsoe_D", "it_nord_entsoe_D", "ch_entsoe_D",
      "be_entsoe_D", "nl_entsoe_D", "at_entsoe_D", "pl_entsoe_D", "cz_entsoe_D",
      "pt_omie_D"), "spot_price (Europa, dia D)"),

    # --- PBF: sustituye al bloque pdbc_* --------------------------------------
    # Se resuelve en `origen()` antes de llegar aqui, porque hay que mirar la medida
    # y no solo el prefijo. La entrada queda como red de seguridad.
    (("pbf_",), "esios_pbf_gen"),

    # --- previsiones ----------------------------------------------------------
    (("ree_ntc_impfr_prev", "ree_ntc_expfr_prev", "ree_ntc_imppt_prev",
      "ree_ntc_exppt_prev", "ree_ntc_impma_prev", "ree_ntc_expma_prev"),
     "forecast (NTC D+1)"),
    (("_prev", "_prev_mw", "c_autoconsumo_prev", "autoconsumo_estimado"), "forecast"),
    (("ree_ntc_",), "load_inter (NTC)"),

    # --- estado real del sistema ----------------------------------------------
    # Las series reales horarias van bajo UN SOLO bloque `generation` aunque procedan de
    # tres tablas (load_inter, entsoe_gen_data, esios_gen): para leer la matriz lo que
    # importa es que son la misma cosa, el estado real del sistema hora a hora.
    (("entsoe_load_", "ree_load_", "ree_netflow_", "total_net_flow_", "gen_peninsular_",
      "wind_mw_", "pumping_cons_mw_", "hydro_run_river_mw_", "biomass_mw_", "waste_mw_",
      "other_renewable_mw_", "oil_mw_", "hydro_dispatch_mw_",
      "ree_g*", "ree_c*", "solar_fv_mw_"), "generation (real D-1/D-6)"),

    # --- resto ----------------------------------------------------------------
    (("gas_", "co2_"), "commodities"),
    (("pdbc_",), "esios_pdbc_gen (RETIRADO)"),
    (("capinst_",), "esios_capacity_installed"),
    (("capdisp_",), "esios_capacity_available"),
    (("t2m_", "d2m_", "msl_", "wind10_", "wind100_", "wind_gust10_", "tcc_", "ssrd_",
      "tp_acum_"), "era5_weather_agg"),
    # `dias_desde_cierre` es de calendario aunque no lleve el prefijo `d1_`. El
    # constructor v5 la excluye por constante, pero clasificarla evita que aparezca
    # como SIN CLASIFICAR en los inventarios.
    (("d1_", "hora_sin", "hora_cos", "dias_desde_cierre"), "CALENDARIO (calculado)"),
]

# Bloque grueso al que pertenece cada tabla, para los resumenes de alto nivel.
BLOQUE = {
    "spot_price (TARGET)": "objetivo",
    "spot_price (lag)": "precio",
    "spot_price (Europa, dia D)": "precio",
    "esios_pbf_gen": "programa PBF",
    "esios_pbf_load_inter": "programa PBF",
    "forecast": "prevision D+1",
    "forecast (NTC D+1)": "prevision D+1",
    "load_inter (NTC)": "prevision D+1",
    "generation (real D-1/D-6)": "real desfasado",
    "commodities": "combustibles",
    "esios_capacity_installed": "capacidad",
    "esios_capacity_available": "capacidad",
    "era5_weather_agg": "meteorologia",
    "CALENDARIO (calculado)": "calendario",
    "CLAVE": "control",
}


def origen(col: str) -> str:
    """Tabla de Postgres de la que procede una columna."""
    # El bloque PBF se resuelve primero: `pbf_net_flow_pt_mw_D` y `pbf_coal_mw_D`
    # comparten prefijo pero salen de tablas distintas.
    if col.startswith("pbf_") and not col.startswith(("pbf_publicado", "pbf_completo")):
        medida = col[len("pbf_"):]
        return "esios_pbf_load_inter" if medida.startswith(_PBF_LOAD) else "esios_pbf_gen"

    for claves, tabla in ORIGEN_COLUMNAS:
        for k in claves:
            if k.startswith("_"):
                if col.endswith(k):
                    return tabla
            elif k.endswith(("_", "*")):
                if col.startswith(k.rstrip("*")):
                    return tabla
            elif col == k:
                return tabla
    return "SIN CLASIFICAR"


def bloque(col: str) -> str:
    """Agrupamiento grueso (objetivo / precio / programa PBF / ...)."""
    return BLOQUE.get(origen(col), "otros")


def con_origen(tabla: pd.DataFrame, col_variable="variable", posicion=1) -> pd.DataFrame:
    """Devuelve la tabla con `tabla_origen` y `bloque` insertadas junto a la variable.

    Sirve para cualquier salida del notebook que liste variables: descriptivos,
    atipicos, perdidos, ranking. Si el nombre de la variable esta en el indice en
    lugar de en una columna, se usa el indice.
    """
    t = tabla.copy()
    nombres = t[col_variable] if col_variable in t.columns else pd.Series(t.index, index=t.index)
    t.insert(min(posicion, len(t.columns)), "tabla_origen", [origen(c) for c in nombres])
    t.insert(min(posicion + 1, len(t.columns)), "bloque", [bloque(c) for c in nombres])
    return t


def con_origen_pares(tabla: pd.DataFrame, col_a="var_a", col_b="var_b") -> pd.DataFrame:
    """Version para tablas de PARES (la de redundancia): anota el origen de los dos.

    La columna `mismo_origen` es la que interesa leer: un par redundante dentro de la
    misma tabla es duplicidad de la fuente; entre tablas distintas es que dos fuentes
    miden lo mismo, y ahi la eleccion es de que publicacion fiarse.
    """
    t = tabla.copy()
    t["origen_a"] = [origen(c) for c in t[col_a]]
    t["origen_b"] = [origen(c) for c in t[col_b]]
    t["mismo_origen"] = t["origen_a"] == t["origen_b"]
    return t


def resumen_origen(columnas) -> pd.DataFrame:
    """Recuento de columnas por tabla de origen y bloque."""
    df = pd.DataFrame({"variable": list(columnas)})
    df["tabla_origen"] = df["variable"].map(origen)
    df["bloque"] = df["variable"].map(bloque)
    return (df.groupby(["bloque", "tabla_origen"])
              .size().rename("columnas").reset_index()
              .sort_values(["bloque", "columnas"], ascending=[True, False])
              .reset_index(drop=True))


def catalogo(columnas) -> pd.DataFrame:
    """Catalogo columna -> tabla -> bloque, para exportar junto a la matriz."""
    return pd.DataFrame({
        "variable": list(columnas),
        "tabla_origen": [origen(c) for c in columnas],
        "bloque": [bloque(c) for c in columnas],
    })
