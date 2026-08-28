"""
bronze_config_v3.py -- entradas de TABLES con los nombres REALES de columna.

Sustituye a `bronze_config_v2_patch.py`. Ya no hay `TODO`: todas las listas salen de la
radiografía de la base del 27-ago-2026 (`eda/columnas_clave.py`), no de suposiciones.

Revisar y pegar en `scripts/bronze_config.py`. Las notas explican por qué está cada columna,
que es lo que hace revisable un registro como éste.

Resumen de lo que cambia respecto del bronce actual
---------------------------------------------------
1. Entra el TARGET (`spot_price`), con 14 columnas de precio: España por tres fuentes,
   Portugal por dos, y diez zonas europeas más.
2. `esios_capacity_available` se DEDUPLICA en la extracción: es un dato diario repetido
   24 veces por día, y unirlo como estaba multiplicaba el calendario x24.
3. `esios_forecast_da` se recorta a sus 4 columnas exclusivas: el resto duplica `forecast`.
4. `trayport_daily_ohlc` sustituye a `commodities` como fuente de gas y CO2: misma
   información y bastante más, desde la misma fecha.
5. `entsoe_load_inter` entra por sus flujos direccionales, que `load_inter` no tiene.
"""

# ===========================================================================
# 1 · EL TARGET
# ===========================================================================

SPOT_PRICE = {
    "spot_price": {
        "grain": "hourly",
        "ts_column": "datetime",
        "prefix": "spot_",
        "columns": [
            # España: tres fuentes contrastadas. Las tres se traen para el bloque F.8; el
            # modelado usa UNA, elegida y documentada de una vez para train y test.
            "es_esios", "es_entsoe", "es_omie",
            # Portugal: sirve para medir el desacople del perímetro peninsular.
            "pt_entsoe", "pt_omie",
            # Resto de zonas SDAC. CON FUGA para D+1 (casación simultánea a las 12:00),
            # pero el precio de D es feature legítima y el análisis de congestión las necesita.
            "fr_entsoe", "de_lu_entsoe", "it_nord_entsoe", "ch_entsoe",
            "be_entsoe", "nl_entsoe", "at_entsoe", "pl_entsoe", "cz_entsoe",
        ],
        "nota": (
            "Target. Las tres columnas españolas coinciden en media (85,84 / 85,84 / 85,85), "
            "así que son intercambiables -- confirmarlo en F.8 y cerrar la elección. "
            "REVISAR ANTES DE MODELAR: el rango español es exactamente [-15, 700] mientras "
            "Francia va de -496,86 a 2.987,78. Topes tan redondos no son comportamiento de "
            "mercado: o hay un límite administrativo o la ingesta recorta. Ver consulta abajo."
        ),
    },
}

# Comprobación de la censura del target, antes de dar por bueno cualquier análisis de colas:
#
#     SELECT COUNT(*) FILTER (WHERE es_omie <= -15)  AS en_el_suelo,
#            COUNT(*) FILTER (WHERE es_omie >= 700)  AS en_el_techo,
#            COUNT(*)                                AS total
#     FROM spot_price;
#
# Si son unas pocas horas, es el mercado tocando un límite real y se documenta. Si son
# muchas, el target está censurado y las colas que se midan no son las verdaderas.


# ===========================================================================
# 2 · PREVISIONES -- las features sin fuga
# ===========================================================================

PREVISIONES = {
    # `forecast` tiene los nombres de la matriz final del equipo. Es la fuente principal.
    "forecast": {
        "grain": "hourly",
        "ts_column": "datetime",
        "prefix": "fc_",
        "columns": [
            "ree_demanda_prev", "c_autoconsumo_prev",
            "ree_gwind_prev", "ree_gsolar_prev", "ree_grenov_prev",
            "ree_ntc_impfr_prev", "ree_ntc_expfr_prev",
            "ree_ntc_imppt_prev", "ree_ntc_exppt_prev",
            "ree_ntc_impma_prev", "ree_ntc_expma_prev",
            "autoconsumo_estimado",   # booleano: el marcador de D-03 ya en la base
        ],
        "nota": (
            "Sin fuga (publicación antes de las 11:00 de D-1). `autoconsumo_estimado` es el "
            "corte de dic-2025 de D-03 ya marcado en origen: no hace falta reconstruirlo con "
            "una fecha a mano. `c_autoconsumo_prev` es la estimación de autoconsumo previsto."
        ),
    },

    # OJO: `esios_forecast_da` es la MISMA información con otros nombres. Verificado:
    # ree_demanda_prev y demanda_mercado_prev_mw coinciden hasta el decimal (8.644 / 41.615 /
    # 27.065,80), igual que eólica, solar, renovables y las seis NTC. Traer las dos tablas
    # enteras sería duplicar doce features. Se traen SÓLO sus columnas exclusivas.
    "esios_forecast_da": {
        "grain": "hourly",
        "ts_column": "datetime",
        "prefix": "esios_fc_",
        "columns": [
            "demanda_prev_mw",           # distinta de demanda_mercado_prev_mw (mín. 14.170 vs 8.644)
            "demanda_residual_prev_mw",  # el maestro la excluye: se revisa 10-14 días después (A.5)
            "gen_solartermica_prev_mw",  # no está en `forecast`
            "cap_baleares_prev_mw",      # no está en `forecast`
        ],
        "nota": (
            "Sólo las 4 columnas que NO duplican `forecast`. Las otras 10 son idénticas hasta "
            "el decimal -- comprobado en la radiografía del 27-ago. Traerlas sería inflar el "
            "conjunto de features con copias."
        ),
    },

    # Segunda fuente de previsión: permite contrastar el error de REE contra el de ENTSO-E,
    # que es el bloque H del EDA vitaminado.
    "entsoe_forecast_da": {
        "grain": "hourly",
        "ts_column": "datetime",
        "prefix": "ent_fc_",
        "columns": ["load_forecast_mw", "wind_forecast_mw",
                    "solar_forecast_mw", "renewables_forecast_mw"],
        "nota": (
            "Sin fuga. Difiere de REE (solar media 4.487 vs 4.186 MW): son dos previsiones "
            "distintas del mismo fenómeno, no una copia. Contrastarlas mide cuánta "
            "incertidumbre hay en la propia entrada del modelo."
        ),
    },

    "ecmwf_forecast_agg": {
        "grain": "3h",
        "ts_column": "ts",
        "prefix": "ecmwf_",
        "columns": ["run_date", "t2m_mean", "d2m_mean", "wind10_mean", "wind100_mean",
                    "wind_gust10_mean", "ssrd_mean", "tcc_mean", "tp_mean", "msl_mean"],
        "nota": (
            "Sólo 168 filas en la TABLA -- ventana móvil, no histórico. PERO las columnas "
            "`tensor_path` y `tensor_index` apuntan a ficheros .npy por `run_date` en el VPS "
            "(/home/ubuntu/scripts/ingesta/tensors/ecmwf_forecast/). Si esos ficheros están "
            "guardados desde hace meses, el histórico de previsión SÍ existe fuera de la base "
            "y la ablación de meteo perfecta se puede hacer. Comprobar con un ls antes de "
            "declararlo como limitación. `ts` es naive pero está en UTC: localizar al unir. "
            "t2m viene en KELVIN (media 297,68): la conversión es de silver, no del bronce."
        ),
    },
}


# ===========================================================================
# 3 · CAPACIDAD -- aquí estaba la duplicación x24
# ===========================================================================

CAPACIDAD = {
    # Verificado en la muestra: las filas de las 19:00, 20:00 y 21:00 del mismo día tienen
    # valores IDÉNTICOS (12.588,9 / 2.085,7 / 7.117,2 / 346,2 / 21.519,6 / 7,9). Es un dato
    # diario repetido 24 veces, no una serie horaria. Se deduplica en la extracción y se
    # mantiene grain="daily"; declararla horaria guardaría 24 copias de cada valor.
    "esios_capacity_available": {
        "grain": "daily",
        "ts_column": "datetime",
        "dedup": True,            # <-- ver deduplicar_diaria() más abajo
        "prefix": "cap_disp_",
        "columns": ["hydro_mw", "pump_mw", "nuclear_mw",
                    "coal_antracita_mw", "ccgt_mw", "fuel_mw"],
        "nota": (
            "58.361 filas / ~2.432 fechas = 24 copias por día. Sin deduplicar, el merge por "
            "date_local multiplica el calendario x24: es el origen del parquet de 1.393.183 "
            "filas. `fuel_mw` es constante en 7,9 MW (D-04): se trae, no es feature. "
            "D-01 sigue abierto: el valor guardado es el conocido a posteriori."
        ),
    },

    "esios_capacity_installed": {
        "grain": "daily",
        "ts_column": "date",
        "prefix": "cap_inst_",
        "columns": [
            "total_mw", "total_renewable_mw", "total_nonrenewable_mw",
            "total_hybrid_mw", "total_autoconsume_mw",
            "hydro_mw", "pump_mw", "wind_mw", "wind_hybrid_mw",
            "solar_pv_mw", "solar_thermal_mw", "solar_pv_hybrid_mw",
            "other_renewable_mw", "waste_nonrenewable_mw", "waste_renewable_mw",
            "battery_hybrid_mw", "autoconsume_solar_pv_mw", "autoconsume_battery_mw",
            "nuclear_mw", "coal_mw", "fuel_mw", "ccgt_mw", "cogeneration_mw",
        ],
        "nota": (
            "Diaria de verdad (2.430 filas, una por fecha). Cinco columnas son CONSTANTES en "
            "seis años y no pueden explicar nada (D-04): pump_mw (3.331,40), nuclear_mw "
            "(7.117,29), ccgt_mw (24.561,85), fuel_mw (7,95) y autoconsume_battery_mw (5,00, "
            "el indicador 2366 congelado en origen). Se traen como evidencia, se excluyen "
            "del modelado. La que sí importa: solar_pv_mw pasa de 8.659 a 54.668 MW, y "
            "autoconsume_solar_pv_mw de 25,6 a 9.125 MW."
        ),
    },
}


# ===========================================================================
# 4 · REALES -- sólo con lag
# ===========================================================================

REALES = {
    "generation": {
        "grain": "hourly",
        "ts_column": "datetime",
        "prefix": "gen_",
        "columns": [
            "c_gsolar", "ree_gsolter", "ent_gwind", "ent_ghydroriver", "c_ghydrodispatch",
            "ent_cpumping", "ree_gbattery", "ree_cbattery", "ent_gbiomass", "ent_gwaste",
            "ent_gotherrenew", "ree_gnuclear", "ree_gccgt", "ree_gotherthermal",
            "ree_gcoal", "ent_goil",
        ],
        "nota": (
            "Con fuga: sólo con lag D-1/D-7. IMPORTANTE: esta tabla YA TRAE las derivadas "
            "calculadas -- `c_gsolar` (media 3.981,38) y `c_ghydrodispatch` (media 2.922,41). "
            "El bronce las recalcula por su cuenta como calc_solar_fv_mw (3.956,57) y "
            "calc_hydro_dispatch_mw (2.925,38). Las medias son parecidas pero NO iguales: hay "
            "dos definiciones circulando. Contrastarlas y quedarse con una."
        ),
    },

    "esios_pdbc_gen": {
        "grain": "hourly",
        "ts_column": "datetime",
        "prefix": "pdbc_",
        "columns": [
            "wind_mw", "solar_pv_mw", "solar_thermal_mw", "hydro_ugh_mw", "hydro_no_ugh_mw",
            "nuclear_mw", "coal_mw", "cogen_mw", "biomass_mw", "biogas_mw", "hybrid_mw",
            "ccgt_mw", "fuel_gas_mw", "waste_mw", "other_renew_mw",
            "pumping_gen_mw", "pumping_cons_mw",
        ],
        "nota": (
            "Programa: contemporáneo es circular (misma casación que el precio), sólo con lag. "
            "OJO al signo: `pumping_cons_mw` es NEGATIVO aquí (de -4.690 a 0), al revés que "
            "en ENTSO-E, que lo guarda positivo. Y tiene NULLs (se ven en la muestra), así que "
            "arrastra el mismo problema de convención del hallazgo B.1 pero con otro signo."
        ),
    },

    # Entra por los flujos DIRECCIONALES, que load_inter no tiene: sólo comparte
    # `datetime` y `total_net_flow_mw` con ella. Para medir congestión y saturación de la
    # interconexión, tener import y export por separado es mejor que el neto.
    "entsoe_load_inter": {
        "grain": "hourly",
        "ts_column": "datetime",
        "prefix": "ent_li_",
        "columns": [
            "actual_load_mw",
            "flow_es_fr_mw", "flow_fr_es_mw", "flow_es_pt_mw", "flow_pt_es_mw",
            "net_flow_fr_mw", "net_flow_pt_mw",
            "ntc_imp_fr_mw", "ntc_exp_fr_mw", "ntc_imp_pt_mw", "ntc_exp_pt_mw",
        ],
        "nota": (
            "Segundo camino para la demanda (`actual_load_mw`) y ÚNICA fuente de flujos "
            "direccionales. Dos usos inmediatos: (a) comprobar si tiene dato bueno en las 9 "
            "horas de ceros espurios de `load_inter_entsoe_load` -- reparar es mejor que "
            "imputar; (b) medir saturación de la interconexión por sentido."
        ),
    },
}


# ===========================================================================
# 5 · COMMODITIES -- trayport sustituye a la tabla actual
# ===========================================================================
#
# `trayport_daily_ohlc` cubre TTF y EUA desde 2020-01-02, la misma ventana que
# `commodities`, y añade: curva forward por `periodo` (Sep-26, Oct-26, Nov-26...),
# apertura/máximo/mínimo/cierre, vwap, volumen, número de operaciones y porcentaje de
# agresor comprador. De ahí salen features que `commodities` no puede dar:
#   - pendiente de la curva forward (contango/backwardation) = expectativa del mercado
#   - rango intradía (máximo - mínimo) = volatilidad realizada
#   - pct_agresor_compra = indicador de presión compradora
#
# Está en FORMATO LARGO: una fila por (fecha, commodity, periodo). Sin pivotar, el merge
# por fecha multiplica el calendario igual que hizo la tabla de capacidad.

def pivotar_trayport(df, metricas=("cierre", "vwap"), solo_primer_periodo=True):
    """
    Largo -> ancho. Con `solo_primer_periodo`, se queda con el contrato más cercano de cada
    commodity (el equivalente al M1 de `commodities`), que es lo que hace comparable la
    serie a lo largo del tiempo.

    ANTES DE USARLA hay que confirmar a qué hora queda fijado el cierre. TTF y EUA cierran
    hacia las 17:30, después del cierre del mercado eléctrico: si es el caso, el último dato
    disponible a las 12:00 de D es el de D-2, igual que en `commodities`. Un dato de mercado
    con la hora de cierre sin verificar es una fuga esperando a ocurrir.
    """
    d = df.copy()
    if solo_primer_periodo:
        d = (d.sort_values(["fecha", "commodity", "item_id"])
               .groupby(["fecha", "commodity"], as_index=False).first())
    ancho = d.pivot_table(index="fecha", columns="commodity", values=list(metricas))
    ancho.columns = [f"tp_{com.lower()}_{met}" for met, com in ancho.columns]
    return ancho.reset_index()


# ===========================================================================
# 6 · Deduplicación y guardarraíles
# ===========================================================================

def deduplicar_diaria(df, ts_column, tz_local="Europe/Madrid"):
    """
    Colapsa una tabla que repite el mismo valor diario en las 24 horas del día a una fila
    por fecha local. Se aplica en la EXTRACCIÓN, antes de guardar el parquet.

    Sin esto, `esios_capacity_available` (58.361 filas para ~2.432 fechas) convierte el
    merge por date_local en muchos-a-muchos y multiplica el calendario entero x24.
    """
    import pandas as pd

    d = df.copy()
    ts = pd.to_datetime(d[ts_column], utc=True)
    d["date_local"] = ts.dt.tz_convert(tz_local).dt.date
    antes = len(d)
    d = d.sort_values(ts_column).drop_duplicates(subset="date_local", keep="last")
    print(f"deduplicar_diaria: {antes:,} -> {len(d):,} filas "
          f"({antes / max(len(d), 1):.1f} copias por fecha)")
    return d.drop(columns=[ts_column])


def verificar_claves(normalized, TABLES):
    """Una fila por clave de join, ANTES de unir. El chequeo que faltaba."""
    problemas = []
    for nombre, df in normalized.items():
        grain = TABLES[nombre].get("grain", "hourly")
        clave = "date_local" if grain == "daily" else "ts_utc"
        if clave not in df.columns:
            problemas.append(f"{nombre}: sin la clave '{clave}' que exige grain='{grain}'")
            continue
        dup = int(df[clave].duplicated().sum())
        print(f"{nombre:28s} grain={grain:6s} filas={len(df):>8,} "
              f"clave={clave:11s} {'ok' if dup == 0 else f'{dup:,} DUPLICADAS'}")
        if dup:
            problemas.append(f"{nombre}: {dup:,} claves duplicadas en '{clave}'")
    if problemas:
        raise ValueError("El merge multiplicaría filas:\n  - " + "\n  - ".join(problemas))
    print("\nTodas las claves son únicas: el merge no puede multiplicar filas.")


def verificar_resultado(bronze_unificado, calendario):
    """El resultado tiene que tener exactamente las filas del calendario."""
    if len(bronze_unificado) != len(calendario):
        raise ValueError(
            f"El merge multiplicó filas: {len(bronze_unificado):,} frente a "
            f"{len(calendario):,} (factor {len(bronze_unificado)/len(calendario):.2f}). "
            "Correr verificar_claves() para ver qué tabla lo provoca."
        )
    print(f"Integridad OK: {len(bronze_unificado):,} filas = {len(calendario):,} horas.")


# ===========================================================================
# 7 · Derivadas nuevas
# ===========================================================================

DERIVED_COLUMNS_V3 = [
    {
        "name": "calc_demanda_residual_prev_mw",
        "inputs": ["fc_ree_demanda_prev", "fc_ree_grenov_prev"],
        "formula": lambda d: d["fc_ree_demanda_prev"] - d["fc_ree_grenov_prev"],
        "nota": (
            "Demanda residual construida SÓLO con previsiones -> SIN FUGA. Es la versión "
            "utilizable del driver que mejor explica el precio. Distinta de "
            "`demanda_residual_prev_mw` de ESIOS, que el maestro excluye porque se revisa "
            "10-14 días después de publicada (A.5). Usa `ree_grenov_prev`, que ya agrega "
            "eólica y solar, en vez de restarlas por separado."
        ),
    },
    {
        "name": "calc_spread_es_fr",
        "inputs": ["spot_es_omie", "spot_fr_entsoe"],
        "formula": lambda d: d["spot_es_omie"] - d["spot_fr_entsoe"],
        "nota": (
            "Diferencial con Francia. |spread| ~ 0 significa mercados acoplados; distinto de "
            "cero, congestión, y el signo da el sentido. CON FUGA para D+1 (casación "
            "simultánea): descriptiva, o con lag de D."
        ),
    },
    {
        "name": "calc_spread_es_pt",
        "inputs": ["spot_es_omie", "spot_pt_omie"],
        "formula": lambda d: d["spot_es_omie"] - d["spot_pt_omie"],
        "nota": (
            "Desacople del perímetro peninsular. El TFM asume que España y Portugal son una "
            "sola zona de precio: esta columna es la que permite verificarlo o declararlo "
            "como limitación."
        ),
    },
]
