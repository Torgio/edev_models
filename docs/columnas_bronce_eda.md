# Columnas de la base — referencia para el EDA

*Generado por `eda/inspect_schema.py` el 27-08-2026 20:08. No editar a mano: se regenera.*

La columna **frontera** no sale de la base: es la clasificación de fuga acordada por el equipo y vive en el diccionario `FRONTERA` de ese script. Si cambia una decisión, se cambia ahí y se regenera este documento.

| Tabla | Filas | Rango temporal | Clave repetida | Frontera |
|---|---|---|---|---|
| [`commodities`](#commodities) | 2,431 | 2020-01-01 → 2026-08-27 | no | CON DESFASE |
| [`ecmwf_forecast_agg`](#ecmwf_forecast_agg) | 168 | 2026-08-08 00:00:00 → 2026-08-28 21:00:00 | no | SIN FUGA |
| [`entsoe_forecast_da`](#entsoe_forecast_da) | 58,343 | 2019-12-31 23:00:00+00:00 → 2026-08-27 21:00:00+00:00 | no | SIN FUGA |
| [`entsoe_gen_data`](#entsoe_gen_data) | 58,319 | 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00 | no | CON FUGA |
| [`entsoe_load_inter`](#entsoe_load_inter) | 58,319 | 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00 | no | CON FUGA |
| [`era5_weather_agg`](#era5_weather_agg) | 19,279 | 2020-01-01 00:00:00 → 2026-08-06 18:00:00 | no | CON FUGA |
| [`esios_capacity_available`](#esios_capacity_available) | 58,361 | 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00 | no | CONDICIONAL |
| [`esios_capacity_installed`](#esios_capacity_installed) | 2,430 | 2020-01-01 → 2026-08-26 | no | SIN FUGA |
| [`esios_forecast_da`](#esios_forecast_da) | 58,367 | 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00 | no | SIN FUGA |
| [`esios_gen`](#esios_gen) | 58,319 | 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00 | no | CON FUGA |
| [`esios_pbf_bilateral`](#esios_pbf_bilateral) | 58,331 | 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00 | no | CON FUGA |
| [`esios_pbf_gen`](#esios_pbf_gen) | 58,367 | 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00 | no | CON FUGA |
| [`esios_pbf_load_inter`](#esios_pbf_load_inter) | 58,367 | 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00 | no | CON FUGA |
| [`esios_pdbc_gen`](#esios_pdbc_gen) | 58,367 | 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00 | no | CON FUGA |
| [`forecast`](#forecast) | 58,367 | 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00 | no | SIN FUGA |
| [`generation`](#generation) | 58,319 | 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00 | no | CON FUGA |
| [`load_inter`](#load_inter) | 58,319 | 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00 | no | CON FUGA |
| [`pipeline_log`](#pipeline_log) | 1,341 | — | — | OPERATIVA |
| [`spot_price`](#spot_price) | 58,361 | 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00 | no | TARGET |
| [`trayport_daily`](#trayport_daily) | 41 | 2026-08-11 → 2026-08-16 | **35** ⚠ | CON DESFASE |
| [`trayport_daily_ohlc`](#trayport_daily_ohlc) | 12,032 | 2020-01-02 → 2026-08-26 | **10,328** ⚠ | CON DESFASE |
| [`trayport_trades`](#trayport_trades) | 1,748,575 | — | — | CON DESFASE |

<a name='commodities'></a>
## `commodities`

**Frontera:** CON DESFASE — TTF y EUA cierran ~17:30 -> el último disponible a las 12:00 de D es D-2.

**Filas:** 2,431
· **Clave temporal:** `fecha` (2,431 valores distintos)
· **Rango:** 2020-01-01 → 2026-08-27

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `fecha` | date | NO | 0.00 |
| `co2_ets` | numeric | YES | 0.00 |
| `gas_ttf` | numeric | YES | 31.18 |
| `gas_mibgas` | numeric | YES | 0.04 |
| `carbon_api2` | numeric | YES | 38.13 |
| `co2_eua_dec` | numeric | YES | 30.23 |
| `gas_ttf_m1` | numeric | YES | 29.95 |

<a name='ecmwf_forecast_agg'></a>
## `ecmwf_forecast_agg`

**Frontera:** SIN FUGA — Previsión meteorológica: la que existe en producción.

**Filas:** 168
· **Clave temporal:** `ts` (168 valores distintos)
· **Rango:** 2026-08-08 00:00:00 → 2026-08-28 21:00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `ts` | timestamp without time zone | NO | 0.00 |
| `run_date` | date | NO | 0.00 |
| `t2m_mean` | double precision | YES | 0.00 |
| `d2m_mean` | double precision | YES | 0.00 |
| `wind10_mean` | double precision | YES | 0.00 |
| `wind100_mean` | double precision | YES | 0.00 |
| `wind_gust10_mean` | double precision | YES | 0.00 |
| `ssrd_mean` | double precision | YES | 0.00 |
| `tcc_mean` | double precision | YES | 0.00 |
| `tp_mean` | double precision | YES | 0.00 |
| `msl_mean` | double precision | YES | 0.00 |
| `tensor_path` | text | YES | 0.00 |
| `tensor_index` | integer | YES | 0.00 |

<a name='entsoe_forecast_da'></a>
## `entsoe_forecast_da`

**Frontera:** SIN FUGA — Previsión day-ahead de ENTSO-E.

**Filas:** 58,343
· **Clave temporal:** `datetime` (58,343 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-27 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `load_forecast_mw` | numeric | YES | 0.04 |
| `wind_forecast_mw` | numeric | YES | 0.70 |
| `solar_forecast_mw` | numeric | YES | 0.33 |
| `renewables_forecast_mw` | numeric | YES | 0.00 |

<a name='entsoe_gen_data'></a>
## `entsoe_gen_data`

**Frontera:** CON FUGA — Generación real ENTSO-E. Usar lag.

**Filas:** 58,319
· **Clave temporal:** `datetime` (58,319 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `updated_at` | timestamp with time zone | YES | 0.00 |
| `solar_mw` | numeric | YES | 0.06 |
| `wind_mw` | numeric | YES | 0.06 |
| `hydro_run_river_mw` | numeric | YES | 0.06 |
| `hydro_reservoir_mw` | numeric | YES | 0.06 |
| `total_hydro_mw` | numeric | YES | 0.00 |
| `pumping_gen_mw` | numeric | YES | 43.85 |
| `pumping_cons_mw` | numeric | YES | 0.06 |
| `battery_gen_mw` | numeric | YES | 87.57 |
| `battery_cons_mw` | numeric | YES | 87.57 |
| `biomass_mw` | numeric | YES | 0.06 |
| `waste_mw` | numeric | YES | 0.06 |
| `other_renewable_mw` | numeric | YES | 0.06 |
| `total_renew_mw` | numeric | YES | 0.00 |
| `nuclear_mw` | numeric | YES | 0.06 |
| `gas_mw` | numeric | YES | 0.06 |
| `coal_mw` | numeric | YES | 0.06 |
| `oil_mw` | numeric | YES | 0.06 |
| `other_thermal_mw` | numeric | YES | 0.06 |
| `total_thermal_mw` | numeric | YES | 0.00 |

<a name='entsoe_load_inter'></a>
## `entsoe_load_inter`

**Frontera:** CON FUGA — Ídem, fuente ENTSO-E. ¿Duplica load_inter?

**Filas:** 58,319
· **Clave temporal:** `datetime` (58,319 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `updated_at` | timestamp with time zone | YES | 0.00 |
| `actual_load_mw` | numeric | YES | 0.06 |
| `flow_es_fr_mw` | numeric | YES | 0.06 |
| `flow_fr_es_mw` | numeric | YES | 0.00 |
| `net_flow_fr_mw` | numeric | YES | 0.00 |
| `ntc_imp_fr_mw` | numeric | YES | 39.83 |
| `ntc_exp_fr_mw` | numeric | YES | 39.83 |
| `flow_es_pt_mw` | numeric | YES | 0.00 |
| `flow_pt_es_mw` | numeric | YES | 0.00 |
| `net_flow_pt_mw` | numeric | YES | 0.00 |
| `ntc_imp_pt_mw` | numeric | YES | 39.83 |
| `ntc_exp_pt_mw` | numeric | YES | 39.83 |
| `total_net_flow_mw` | numeric | YES | 0.00 |
| `net_load_mw` | numeric | YES | 0.00 |

<a name='era5_weather_agg'></a>
## `era5_weather_agg`

**Frontera:** CON FUGA — Reanálisis. Sólo con lag o como ablación de meteo perfecta.

**Filas:** 19,279
· **Clave temporal:** `ts` (19,279 valores distintos)
· **Rango:** 2020-01-01 00:00:00 → 2026-08-06 18:00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `ts` | timestamp without time zone | NO | 0.00 |
| `t2m_mean` | double precision | YES | 0.00 |
| `wind10_mean` | double precision | YES | 0.00 |
| `wind100_mean` | double precision | YES | 0.00 |
| `ssrd_mean` | double precision | YES | 0.00 |
| `tcc_mean` | double precision | YES | 0.00 |
| `tensor_path` | text | YES | 0.00 |
| `d2m_mean` | double precision | YES | 0.00 |
| `wind_gust10_mean` | double precision | YES | 1.24 |
| `tp_mean` | double precision | YES | 0.00 |
| `msl_mean` | double precision | YES | 0.00 |
| `tensor_index` | integer | YES | 0.00 |

<a name='esios_capacity_available'></a>
## `esios_capacity_available`

**Frontera:** CONDICIONAL — D-01: la ingesta guarda una fila por date creada a las 21:05.

**Filas:** 58,361
· **Clave temporal:** `datetime` (58,361 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `hydro_mw` | double precision | YES | 0.00 |
| `pump_mw` | double precision | YES | 0.00 |
| `nuclear_mw` | double precision | YES | 0.00 |
| `coal_antracita_mw` | double precision | YES | 0.01 |
| `ccgt_mw` | double precision | YES | 0.00 |
| `fuel_mw` | double precision | YES | 0.00 |

<a name='esios_capacity_installed'></a>
## `esios_capacity_installed`

**Frontera:** SIN FUGA — Potencia instalada. Varias columnas constantes (D-04).

**Filas:** 2,430
· **Clave temporal:** `date` (2,430 valores distintos)
· **Rango:** 2020-01-01 → 2026-08-26

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `date` | date | NO | 0.00 |
| `total_mw` | numeric | YES | 0.00 |
| `total_renewable_mw` | numeric | YES | 0.00 |
| `total_nonrenewable_mw` | numeric | YES | 0.00 |
| `total_hybrid_mw` | numeric | YES | 0.00 |
| `total_autoconsume_mw` | numeric | YES | 0.00 |
| `hydro_mw` | numeric | YES | 0.00 |
| `pump_mw` | numeric | YES | 0.00 |
| `wind_mw` | numeric | YES | 0.00 |
| `wind_hybrid_mw` | numeric | YES | 58.81 |
| `solar_pv_mw` | numeric | YES | 0.00 |
| `solar_thermal_mw` | numeric | YES | 0.00 |
| `solar_pv_hybrid_mw` | numeric | YES | 51.32 |
| `other_renewable_mw` | numeric | YES | 0.00 |
| `waste_nonrenewable_mw` | numeric | YES | 0.00 |
| `waste_renewable_mw` | numeric | YES | 0.00 |
| `battery_hybrid_mw` | numeric | YES | 60.12 |
| `autoconsume_solar_pv_mw` | numeric | YES | 0.00 |
| `autoconsume_battery_mw` | numeric | YES | 50.04 |
| `nuclear_mw` | numeric | YES | 0.00 |
| `coal_mw` | numeric | YES | 0.00 |
| `fuel_mw` | numeric | YES | 0.00 |
| `ccgt_mw` | numeric | YES | 0.00 |
| `cogeneration_mw` | numeric | YES | 0.00 |

<a name='esios_forecast_da'></a>
## `esios_forecast_da`

**Frontera:** SIN FUGA — Previsión day-ahead de ESIOS.

**Filas:** 58,367
· **Clave temporal:** `datetime` (58,367 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `demanda_prev_mw` | numeric | YES | 0.00 |
| `gen_wind_prev_mw` | numeric | YES | 0.00 |
| `gen_solar_pv_prev_mw` | numeric | YES | 0.00 |
| `gen_renovables_prev_mw` | numeric | YES | 0.00 |
| `demanda_residual_prev_mw` | numeric | YES | 0.01 |
| `ntc_fr_imp_prev_mw` | numeric | YES | 12.60 |
| `ntc_fr_exp_prev_mw` | numeric | YES | 12.60 |
| `ntc_pt_imp_prev_mw` | numeric | YES | 12.60 |
| `ntc_pt_exp_prev_mw` | numeric | YES | 12.60 |
| `ntc_ma_imp_prev_mw` | numeric | YES | 12.60 |
| `ntc_ma_exp_prev_mw` | numeric | YES | 12.60 |
| `demanda_mercado_prev_mw` | numeric | YES | 0.01 |
| `gen_solartermica_prev_mw` | numeric | YES | 0.05 |
| `cap_baleares_prev_mw` | numeric | YES | 0.00 |

<a name='esios_gen'></a>
## `esios_gen`

**Frontera:** CON FUGA — Generación real ESIOS. Usar lag.

**Filas:** 58,319
· **Clave temporal:** `datetime` (58,319 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `updated_at` | timestamp with time zone | YES | 0.00 |
| `ree_gsolar_mw` | numeric | YES | 0.00 |
| `ree_gsolter_mw` | numeric | YES | 0.00 |
| `ree_gwind_mw` | numeric | YES | 0.00 |
| `ree_ghidro_mw` | numeric | YES | 0.00 |
| `ree_gpumping_mw` | numeric | YES | 0.01 |
| `ree_cpumping_mw` | numeric | YES | 0.01 |
| `ree_gbattery_mw` | numeric | YES | 74.64 |
| `ree_cbattery_mw` | numeric | YES | 74.64 |
| `ree_gtermicarenew_mw` | numeric | YES | 0.00 |
| `ree_grenew_mw` | numeric | YES | 0.00 |
| `ree_gnorenew_mw` | numeric | YES | 0.00 |
| `ree_gnuclear_mw` | numeric | YES | 0.00 |
| `ree_gccgas_mw` | numeric | YES | 0.00 |
| `ree_gcoal_mw` | numeric | YES | 0.00 |
| `ree_goil_mw` | numeric | YES | 95.83 |
| `ree_gotherthermal_mw` | numeric | YES | 0.00 |
| `ree_gtotalthermal_mw` | numeric | YES | 0.00 |
| `ree_gtotal_mw` | numeric | YES | 0.00 |
| `ree_ghidro_pura_mw` | numeric | YES | 100.00 |

<a name='esios_pbf_bilateral'></a>
## `esios_pbf_bilateral`

**Frontera:** CON FUGA — Programa base, contratación bilateral.

**Filas:** 58,331
· **Clave temporal:** `datetime` (58,331 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `updated_at` | timestamp with time zone | YES | 0.00 |
| `bil_hydro_ugh_mw` | numeric | YES | 0.00 |
| `bil_nuclear_mw` | numeric | YES | 1.10 |
| `bil_coal_mw` | numeric | YES | 91.18 |
| `bil_cogen_mw` | numeric | YES | 48.55 |
| `bil_wind_onshore_mw` | numeric | YES | 0.04 |
| `bil_solar_pv_mw` | numeric | YES | 41.38 |
| `bil_hybrid_mw` | numeric | YES | 74.41 |
| `bil_retail_free_sales_mw` | numeric | YES | 0.00 |
| `bil_retail_free_buy_mw` | numeric | YES | 0.00 |
| `bil_retail_last_resort_mw` | numeric | YES | 0.00 |
| `bil_direct_consumer_mw` | numeric | YES | 16.27 |
| `bil_total_sales_mw` | numeric | YES | 0.00 |
| `bil_total_purchases_mw` | numeric | YES | 0.00 |
| `bil_hydro_no_ugh_mw` | numeric | YES | 26.67 |
| `bil_solar_thermal_mw` | numeric | YES | 84.44 |
| `bil_petro_coal_mw` | numeric | YES | 87.12 |
| `bil_biomass_mw` | numeric | YES | 40.21 |
| `bil_biogas_mw` | numeric | YES | 13.14 |
| `bil_generic_sales_mw` | numeric | YES | 0.04 |
| `bil_generic_buy_mw` | numeric | YES | 0.06 |

<a name='esios_pbf_gen'></a>
## `esios_pbf_gen`

**Frontera:** CON FUGA — Programa base de funcionamiento.

**Filas:** 58,367
· **Clave temporal:** `datetime` (58,367 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `updated_at` | timestamp with time zone | YES | 0.00 |
| `wind_mw` | numeric | YES | 0.06 |
| `solar_pv_mw` | numeric | YES | 1.35 |
| `solar_thermal_mw` | numeric | YES | 2.95 |
| `hydro_no_ugh_mw` | numeric | YES | 0.06 |
| `hydro_ugh_mw` | numeric | YES | 0.06 |
| `total_hydro_mw` | numeric | YES | 0.06 |
| `pumping_gen_mw` | numeric | YES | 0.08 |
| `pumping_cons_mw` | numeric | YES | 0.11 |
| `biomass_mw` | numeric | YES | 0.06 |
| `biogas_mw` | numeric | YES | 0.06 |
| `waste_mw` | numeric | YES | 0.06 |
| `other_renew_mw` | numeric | YES | 0.06 |
| `nuclear_mw` | numeric | YES | 0.20 |
| `ccgt_mw` | numeric | YES | 0.00 |
| `cogen_mw` | numeric | YES | 0.06 |
| `coal_mw` | numeric | YES | 0.00 |
| `fuel_gas_mw` | numeric | YES | 0.00 |
| `hybrid_mw` | numeric | YES | 0.00 |
| `total_gen_mw` | numeric | YES | 0.07 |
| `unavailable_power_mw` | numeric | YES | 0.00 |

<a name='esios_pbf_load_inter'></a>
## `esios_pbf_load_inter`

**Frontera:** CON FUGA — Programa base, demanda e interconexiones.

**Filas:** 58,367
· **Clave temporal:** `datetime` (58,367 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `updated_at` | timestamp with time zone | YES | 0.00 |
| `demand_free_market_mw` | numeric | YES | 0.06 |
| `demand_reference_mw` | numeric | YES | 0.06 |
| `demand_direct_mw` | numeric | YES | 0.06 |
| `demand_aux_mw` | numeric | YES | 0.06 |
| `total_demand_mw` | numeric | YES | 0.06 |
| `net_flow_fr_mw` | numeric | YES | 0.05 |
| `net_flow_pt_mw` | numeric | YES | 0.24 |
| `net_flow_ma_mw` | numeric | YES | 53.78 |
| `net_flow_ad_mw` | numeric | YES | 12.16 |
| `total_net_flow_mw` | numeric | YES | 0.00 |
| `baleares_mw` | numeric | YES | 1.84 |

<a name='esios_pdbc_gen'></a>
## `esios_pdbc_gen`

**Frontera:** CON FUGA — Misma casación que el precio -> circular en contemporáneo.

**Filas:** 58,367
· **Clave temporal:** `datetime` (58,367 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `wind_mw` | numeric | YES | 0.06 |
| `solar_pv_mw` | numeric | YES | 1.35 |
| `solar_thermal_mw` | numeric | YES | 2.95 |
| `hydro_ugh_mw` | numeric | YES | 0.06 |
| `hydro_no_ugh_mw` | numeric | YES | 0.06 |
| `nuclear_mw` | numeric | YES | 0.20 |
| `coal_mw` | numeric | YES | 0.00 |
| `cogen_mw` | numeric | YES | 0.06 |
| `biomass_mw` | numeric | YES | 0.06 |
| `biogas_mw` | numeric | YES | 0.06 |
| `hybrid_mw` | numeric | YES | 0.00 |
| `ccgt_mw` | numeric | YES | 0.00 |
| `fuel_gas_mw` | numeric | YES | 0.00 |
| `waste_mw` | numeric | YES | 0.06 |
| `other_renew_mw` | numeric | YES | 0.06 |
| `pumping_gen_mw` | numeric | YES | 35.52 |
| `pumping_cons_mw` | numeric | YES | 59.46 |

<a name='forecast'></a>
## `forecast`

**Frontera:** SIN FUGA — Previsiones REE de D+1 publicadas antes de las 11:00 de D-1 (Circular 4/2019).

**Filas:** 58,367
· **Clave temporal:** `datetime` (58,367 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `ree_demanda_prev` | numeric | YES | 0.01 |
| `c_autoconsumo_prev` | numeric | YES | 0.01 |
| `ree_gwind_prev` | numeric | YES | 0.00 |
| `ree_gsolar_prev` | numeric | YES | 0.00 |
| `ree_grenov_prev` | numeric | YES | 0.00 |
| `ree_ntc_impfr_prev` | numeric | YES | 12.60 |
| `ree_ntc_expfr_prev` | numeric | YES | 12.60 |
| `ree_ntc_imppt_prev` | numeric | YES | 12.60 |
| `ree_ntc_exppt_prev` | numeric | YES | 12.60 |
| `ree_ntc_impma_prev` | numeric | YES | 12.60 |
| `ree_ntc_expma_prev` | numeric | YES | 12.60 |
| `autoconsumo_estimado` | boolean | YES | 0.00 |

<a name='generation'></a>
## `generation`

**Frontera:** CON FUGA — Generación real. Usar lag D-1/D-7.

**Filas:** 58,319
· **Clave temporal:** `datetime` (58,319 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `c_gsolar` | numeric | YES | 0.06 |
| `ree_gsolter` | numeric | YES | 0.00 |
| `ent_gwind` | numeric | YES | 0.06 |
| `ent_ghydroriver` | numeric | YES | 0.06 |
| `c_ghydrodispatch` | numeric | YES | 0.06 |
| `ent_cpumping` | numeric | YES | 0.06 |
| `ree_gbattery` | numeric | YES | 74.64 |
| `ree_cbattery` | numeric | YES | 74.64 |
| `ent_gbiomass` | numeric | YES | 0.06 |
| `ent_gwaste` | numeric | YES | 0.06 |
| `ent_gotherrenew` | numeric | YES | 0.06 |
| `ree_gnuclear` | numeric | YES | 0.00 |
| `ree_gccgt` | numeric | YES | 0.00 |
| `ree_gotherthermal` | numeric | YES | 0.00 |
| `ree_gcoal` | numeric | YES | 0.00 |
| `ent_goil` | numeric | YES | 0.06 |

<a name='load_inter'></a>
## `load_inter`

**Frontera:** CON FUGA — Demanda e interconexiones reales. Usar lag.

**Filas:** 58,319
· **Clave temporal:** `datetime` (58,319 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-26 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `entsoe_load` | numeric | YES | 0.06 |
| `ree_load` | numeric | YES | 0.00 |
| `ree_ntc_impfr` | numeric | YES | 0.00 |
| `ree_ntc_expfr` | numeric | YES | 0.00 |
| `ree_netflow_fr` | numeric | YES | 0.00 |
| `ree_ntc_imppt` | numeric | YES | 0.00 |
| `ree_ntc_exppt` | numeric | YES | 0.00 |
| `ree_netflow_pt` | numeric | YES | 0.00 |
| `ree_ntc_impma` | numeric | YES | 0.00 |
| `ree_ntc_expma` | numeric | YES | 0.00 |
| `ree_netflow_ma` | numeric | YES | 0.00 |
| `total_net_flow_mw` | numeric | YES | 0.00 |
| `gen_peninsular_mw` | numeric | YES | 0.06 |

<a name='pipeline_log'></a>
## `pipeline_log`

**Frontera:** OPERATIVA — Metadatos de ingesta. No es feature: sirve para auditar huecos.

**Filas:** 1,341

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `id` | integer | NO | 0.00 |
| `pipeline` | text | YES | 0.00 |
| `fecha_inicio` | date | YES | 0.00 |
| `fecha_fin` | date | YES | 0.00 |
| `registros` | integer | YES | 0.00 |
| `estado` | text | YES | 0.00 |
| `mensaje` | text | YES | 0.00 |
| `duracion_seg` | numeric | YES | 0.00 |
| `created_at` | timestamp with time zone | YES | 0.00 |

<a name='spot_price'></a>
## `spot_price`

**Frontera:** TARGET — Casación 12:00 de D, publicación ~12:45. El de D+1 es lo que se predice.

**Filas:** 58,361
· **Clave temporal:** `datetime` (58,361 valores distintos)
· **Rango:** 2019-12-31 23:00:00+00:00 → 2026-08-28 21:00:00+00:00

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `datetime` | timestamp with time zone | NO | 0.00 |
| `es_esios` | numeric | YES | 0.00 |
| `es_entsoe` | numeric | YES | 0.00 |
| `es_omie` | numeric | YES | 0.04 |
| `pt_entsoe` | numeric | YES | 0.00 |
| `pt_omie` | numeric | YES | 0.04 |
| `fr_entsoe` | numeric | YES | 0.00 |
| `de_lu_entsoe` | numeric | YES | 0.00 |
| `it_nord_entsoe` | numeric | YES | 0.04 |
| `ch_entsoe` | numeric | YES | 0.00 |
| `be_entsoe` | numeric | YES | 0.00 |
| `nl_entsoe` | numeric | YES | 0.00 |
| `at_entsoe` | numeric | YES | 0.00 |
| `pl_entsoe` | numeric | YES | 0.00 |
| `cz_entsoe` | numeric | YES | 0.00 |

<a name='trayport_daily'></a>
## `trayport_daily`

**Frontera:** CON DESFASE — Verificar hora de cierre antes de decidir el desfase.

**Filas:** 41
· **Clave temporal:** `fecha` (6 valores distintos)  ⚠ **35 filas con marca temporal repetida**
· **Rango:** 2026-08-11 → 2026-08-16

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `fecha` | date | NO | 0.00 |
| `commodity` | text | NO | 0.00 |
| `periodo` | text | NO | 0.00 |
| `item_id` | integer | NO | 0.00 |
| `precio` | numeric | YES | 0.00 |
| `fuente` | text | YES | 0.00 |
| `deal_date` | timestamp with time zone | YES | 14.63 |
| `antiguedad_h` | integer | YES | 14.63 |
| `updated_at` | timestamp with time zone | YES | 0.00 |

<a name='trayport_daily_ohlc'></a>
## `trayport_daily_ohlc`

**Frontera:** CON DESFASE — Ídem.

**Filas:** 12,032
· **Clave temporal:** `fecha` (1,704 valores distintos)  ⚠ **10,328 filas con marca temporal repetida**
· **Rango:** 2020-01-02 → 2026-08-26

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `fecha` | date | NO | 0.00 |
| `commodity` | text | NO | 0.00 |
| `periodo` | text | NO | 0.00 |
| `item_id` | integer | YES | 0.00 |
| `apertura` | numeric | YES | 0.00 |
| `maximo` | numeric | YES | 0.00 |
| `minimo` | numeric | YES | 0.00 |
| `cierre` | numeric | YES | 0.00 |
| `vwap` | numeric | YES | 0.00 |
| `volumen` | integer | YES | 0.00 |
| `n_trades` | integer | YES | 0.00 |
| `pct_agresor_compra` | numeric | YES | 0.00 |

<a name='trayport_trades'></a>
## `trayport_trades`

**Frontera:** CON DESFASE — Ídem. Verificar además granularidad y unidad.

**Filas:** 1,748,575

| Columna | Tipo | Nullable | % nulos |
|---|---|---|---|
| `trade_id` | text | NO | 0.00 |
| `commodity` | text | NO | 0.00 |
| `periodo` | text | NO | 0.00 |
| `item_id` | integer | NO | 0.00 |
| `deal_date` | timestamp with time zone | NO | 0.00 |
| `precio` | numeric | NO | 0.00 |
| `cantidad` | integer | YES | 0.00 |
| `agresor_compra` | boolean | YES | 0.00 |
| `venue` | text | YES | 0.00 |