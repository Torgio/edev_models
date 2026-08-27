# Columnas clave de las tablas principales

*Generado por `eda/columnas_clave.py` el 27-08-2026 21:31.*

Complementa `columnas_bronce_eda.md` (que cubre las 22 tablas) con el detalle de las que van a entrar al bronce: rangos de valores reales, que son los que revelan la unidad, y una muestra de filas.

## `spot_price`

*TARGET. Con varias fuentes contrastadas: traerlas TODAS (bloque F.8).*

**Clave temporal:** `datetime` · **grain propuesto:** `hourly` · **prefijo:** `spot_`

| Columna | min | max | media |
|---|---|---|---|
| `es_esios` | -15.0 | 700.0 | 85.84 |
| `es_entsoe` | -15.0 | 700.0 | 85.84 |
| `es_omie` | -15.0 | 700.0 | 85.85 |
| `pt_entsoe` | -9.83 | 651.0 | 86.27 |
| `pt_omie` | -9.83 | 651.0 | 86.28 |
| `fr_entsoe` | -496.86 | 2987.78 | 102.36 |
| `de_lu_entsoe` | -500.0 | 936.28 | 104.16 |
| `it_nord_entsoe` | 0.0 | 871.0 | 137.0 |
| `ch_entsoe` | -463.68 | 871.61 | 118.86 |
| `be_entsoe` | -497.91 | 933.28 | 104.68 |
| `nl_entsoe` | -500.0 | 872.96 | 105.81 |
| `at_entsoe` | -500.0 | 919.64 | 114.07 |
| `pl_entsoe` | -469.23 | 771.0 | 103.22 |
| `cz_entsoe` | -500.0 | 871.0 | 110.7 |

## `forecast`

*Sin fuga. Los nombres de la matriz final del equipo.*

**Clave temporal:** `datetime` · **grain propuesto:** `hourly` · **prefijo:** `fc_`

| Columna | min | max | media |
|---|---|---|---|
| `ree_demanda_prev` | 8644.0 | 41615.0 | 27065.8 |
| `c_autoconsumo_prev` | -8716.0 | 23263.0 | 166.9 |
| `ree_gwind_prev` | 452.8 | 20879.3 | 6676.35 |
| `ree_gsolar_prev` | 0.0 | 31299.8 | 4186.34 |
| `ree_grenov_prev` | 703.5 | 42319.8 | 10862.69 |
| `ree_ntc_impfr_prev` | 0.0 | 3977.0 | 2716.47 |
| `ree_ntc_expfr_prev` | 0.0 | 4070.0 | 2351.74 |
| `ree_ntc_imppt_prev` | 0.0 | 5850.0 | 3077.43 |
| `ree_ntc_exppt_prev` | 0.0 | 6615.0 | 3664.16 |
| `ree_ntc_impma_prev` | 0.0 | 600.0 | 582.77 |
| `ree_ntc_expma_prev` | 0.0 | 900.0 | 855.74 |

## `esios_forecast_da`

*Sin fuga. El bronce sólo trae 3 de sus columnas.*

**Clave temporal:** `datetime` · **grain propuesto:** `hourly` · **prefijo:** `esios_fc_`

| Columna | min | max | media |
|---|---|---|---|
| `demanda_prev_mw` | 14170.0 | 42399.0 | 27231.84 |
| `gen_wind_prev_mw` | 452.8 | 20879.3 | 6676.35 |
| `gen_solar_pv_prev_mw` | 0.0 | 31299.8 | 4186.34 |
| `gen_renovables_prev_mw` | 703.5 | 42319.8 | 10862.69 |
| `demanda_residual_prev_mw` | -11538.35 | 38472.7 | 15887.21 |
| `ntc_fr_imp_prev_mw` | 0.0 | 3977.0 | 2716.47 |
| `ntc_fr_exp_prev_mw` | 0.0 | 4070.0 | 2351.74 |
| `ntc_pt_imp_prev_mw` | 0.0 | 5850.0 | 3077.43 |
| `ntc_pt_exp_prev_mw` | 0.0 | 6615.0 | 3664.16 |
| `ntc_ma_imp_prev_mw` | 0.0 | 600.0 | 582.77 |
| `ntc_ma_exp_prev_mw` | 0.0 | 900.0 | 855.74 |
| `demanda_mercado_prev_mw` | 8644.0 | 41615.0 | 27065.8 |
| `gen_solartermica_prev_mw` | 0.0 | 2192.5 | 504.31 |
| `cap_baleares_prev_mw` | 0.0 | 408.0 | 149.68 |

## `entsoe_forecast_da`

*Sin fuga. Segunda fuente de previsión: permite contraste.*

**Clave temporal:** `datetime` · **grain propuesto:** `hourly` · **prefijo:** `ent_fc_`

| Columna | min | max | media |
|---|---|---|---|
| `load_forecast_mw` | 15330.0 | 41786.0 | 27066.82 |
| `wind_forecast_mw` | 346.75 | 19753.75 | 6629.65 |
| `solar_forecast_mw` | 0.0 | 29023.75 | 4489.37 |
| `renewables_forecast_mw` | 0.0 | 38602.75 | 11057.72 |

## `esios_capacity_available`

*OJO: es HORARIA (58.361 filas), no diaria. Ver diagnóstico 1.*

**Clave temporal:** `datetime` · **grain propuesto:** `hourly` · **prefijo:** `cap_disp_`

| Columna | min | max | media |
|---|---|---|---|
| `hydro_mw` | 9140.6 | 14265.7 | 12629.65 |
| `pump_mw` | 844.5 | 3417.5 | 2646.01 |
| `nuclear_mw` | 3173.1 | 7117.2 | 6447.09 |
| `coal_antracita_mw` | 20.0 | 4185.3 | 990.65 |
| `ccgt_mw` | 13522.9 | 23334.5 | 19876.25 |
| `fuel_mw` | 7.9 | 7.9 | 7.9 |

## `esios_capacity_installed`

*Diaria de verdad (2.430 filas). Varias columnas constantes (D-04).*

**Clave temporal:** `date` · **grain propuesto:** `daily` · **prefijo:** `cap_inst_`

| Columna | min | max | media |
|---|---|---|---|
| `total_mw` | 101494.34 | 147224.19 | 118461.75 |
| `total_renewable_mw` | 54550.69 | 108139.86 | 77228.03 |
| `total_nonrenewable_mw` | 39084.33 | 46943.65 | 41233.72 |
| `total_hybrid_mw` | 11.58 | 1752.79 | 320.91 |
| `total_autoconsume_mw` | 1292.57 | 11189.62 | 5625.92 |
| `hydro_mw` | 17073.86 | 17097.25 | 17090.91 |
| `pump_mw` | 3331.4 | 3331.4 | 3331.4 |
| `wind_mw` | 25282.98 | 32867.41 | 29584.97 |
| `wind_hybrid_mw` | 94.8 | 655.13 | 330.9 |
| `solar_pv_mw` | 8658.8 | 54667.67 | 27018.85 |
| `solar_thermal_mw` | 2301.94 | 2303.33 | 2302.42 |
| `solar_pv_hybrid_mw` | 5.97 | 795.77 | 308.46 |
| `other_renewable_mw` | 1089.9 | 1145.85 | 1101.34 |
| `waste_nonrenewable_mw` | 378.35 | 406.63 | 391.86 |
| `waste_renewable_mw` | 118.84 | 131.63 | 129.54 |
| `battery_hybrid_mw` | 5.0 | 235.03 | 44.47 |
| `autoconsume_solar_pv_mw` | 25.58 | 9125.05 | 3550.93 |
| `autoconsume_battery_mw` | 5.0 | 5.0 | 5.0 |
| `nuclear_mw` | 7117.29 | 7117.29 | 7117.29 |
| `coal_mw` | 1258.09 | 9215.05 | 3552.63 |
| `fuel_mw` | 7.95 | 7.95 | 7.95 |
| `ccgt_mw` | 24561.85 | 24561.85 | 24561.85 |
| `cogeneration_mw` | 5193.47 | 5638.01 | 5504.57 |

## `generation`

*Con fuga: sólo con lag D-1/D-7.*

**Clave temporal:** `datetime` · **grain propuesto:** `hourly` · **prefijo:** `gen_`

| Columna | min | max | media |
|---|---|---|---|
| `c_gsolar` | 0.0 | 28203.08 | 3981.38 |
| `ree_gsolter` | -126.58 | 2179.33 | 493.25 |
| `ent_gwind` | 181.0 | 20718.0 | 6546.13 |
| `ent_ghydroriver` | 253.0 | 1994.0 | 954.88 |
| `c_ghydrodispatch` | 141.0 | 11045.0 | 2922.41 |
| `ent_cpumping` | 0.0 | 4620.0 | 852.87 |
| `ree_gbattery` | 0.0 | 50.75 | 1.55 |
| `ree_cbattery` | -51.33 | 28.0 | -1.87 |
| `ent_gbiomass` | 84.0 | 609.0 | 421.47 |
| `ent_gwaste` | 28.0 | 349.0 | 237.23 |
| `ent_gotherrenew` | 18.0 | 131.0 | 85.01 |
| `ree_gnuclear` | 0.0 | 7139.92 | 6155.77 |
| `ree_gccgt` | 0.0 | 17656.17 | 4653.92 |
| `ree_gotherthermal` | 0.0 | 4022.83 | 2263.83 |
| `ree_gcoal` | 0.0 | 2346.83 | 456.64 |
| `ent_goil` | 0.0 | 321.0 | 92.38 |

## `esios_pdbc_gen`

*Con fuga: contemporáneo es circular.*

**Clave temporal:** `datetime` · **grain propuesto:** `hourly` · **prefijo:** `pdbc_`

| Columna | min | max | media |
|---|---|---|---|
| `wind_mw` | 268.6 | 20657.0 | 6246.52 |
| `solar_pv_mw` | 0.0 | 25778.8 | 3624.67 |
| `solar_thermal_mw` | 0.1 | 2186.3 | 675.66 |
| `hydro_ugh_mw` | 21.2 | 7095.7 | 1021.77 |
| `hydro_no_ugh_mw` | 132.4 | 1303.0 | 582.12 |
| `nuclear_mw` | 0.0 | 7092.3 | 1509.91 |
| `coal_mw` | 0.0 | 2440.0 | 177.87 |
| `cogen_mw` | 569.2 | 3709.2 | 2253.72 |
| `biomass_mw` | 97.3 | 565.3 | 405.64 |
| `biogas_mw` | 23.1 | 155.9 | 83.39 |
| `hybrid_mw` | -2.3 | 528.4 | 33.79 |
| `ccgt_mw` | 0.0 | 15665.8 | 2109.99 |
| `fuel_gas_mw` | 0.0 | 500.0 | 0.85 |
| `waste_mw` | 32.0 | 450.2 | 295.07 |
| `other_renew_mw` | 124.8 | 687.3 | 510.27 |
| `pumping_gen_mw` | 0.0 | 2648.9 | 620.73 |
| `pumping_cons_mw` | -4690.0 | 0.0 | -1466.54 |

## `ecmwf_forecast_agg`

*SÓLO 168 FILAS: ventana móvil, no histórico. Ver aviso al final.*

**Clave temporal:** `ts` · **grain propuesto:** `3h` · **prefijo:** `ecmwf_`

| Columna | min | max | media |
|---|---|---|---|
| `t2m_mean` | 290.8091735839844 | 304.5615234375 | 297.68 |
| `d2m_mean` | 284.3954162597656 | 291.335205078125 | 289.5 |
| `wind10_mean` | 1.906479001045227 | 5.618484020233154 | 3.4 |
| `wind100_mean` | 2.2513532638549805 | 7.472042560577393 | 4.69 |
| `wind_gust10_mean` | 3.413365364074707 | 11.437806129455566 | 6.55 |
| `ssrd_mean` | 0.0 | 808.8405151367188 | 265.37 |
| `tcc_mean` | 0.06984316557645798 | 0.7173461318016052 | 0.31 |
| `tp_mean` | 0.00027581010363064706 | 1.1865878105163574 | 0.13 |
| `msl_mean` | 101056.6328125 | 102063.328125 | 101547.69 |
| `tensor_index` | 0.0 | 7.0 | 3.5 |

## `trayport_daily_ohlc`

*Formato largo (~7 filas/fecha): hay que pivotar antes de unir.*

**Clave temporal:** `fecha` · **grain propuesto:** `daily` · **prefijo:** `tp_`

| Columna | min | max | media |
|---|---|---|---|
| `item_id` | 194.0 | 910.0 | 390.17 |
| `apertura` | 3.55 | 331.0 | 55.85 |
| `maximo` | 3.775 | 347.8 | 56.85 |
| `minimo` | 3.375 | 319.0 | 54.9 |
| `cierre` | 3.525 | 335.4 | 55.79 |
| `vwap` | 3.612 | 341.37 | 55.85 |
| `volumen` | 1.0 | 6937.0 | 268.05 |
| `n_trades` | 1.0 | 3733.0 | 145.33 |
| `pct_agresor_compra` | 0.0 | 100.0 | 23.79 |
