# Pipeline de bronze — TFM Energía UCM

Capa bronze del pipeline medallón: une tablas fuente sobre un eje temporal común, mínimamente transformadas, lista para EDA. No imputa, no trata outliers, no deduplica — eso es silver.

## Alcance actual

6 tablas: `entsoe_gen_data`, `esios_gen`, `load_inter`, `esios_forecast_da`, `commodities`, `era5_weather_agg`. Columnas seleccionadas según la matriz de decisiones del equipo (generación, demanda/interconexión, forecast, commodities) — no `SELECT *`. Ver `columnas_bronce_eda.md` para el detalle completo, tabla por tabla.

`esios_marketdata` excluida: se borró de la base. `esios_pdbc` excluida: no existe todavía. `esios_capacity_*` pendientes de sumar (necesarias para validar D-01/D-04 en el EDA).

## Columnas seleccionadas vs. columnas de evidencia

La mayoría de las columnas son para modelar (coinciden con lo que usaría el dataset de `construir_dataset_maestro.py`). Un subconjunto chico se trae **solo para poder reproducir con evidencia propia** una decisión ya documentada por el equipo, aunque esa columna esté descartada del modelado:

- `entsoe_gas_mw`: descartada por D-02 (mezcla CCGT + cogeneración) — se mantiene para reproducir la comparación contra `ree_gccgas_mw`/`ree_gotherthermal_mw`.
- `esios_gen_ree_gsolar_mw`: descartada por D-03 (autoconsumo desde dic-2025) — se mantiene para reproducir la comparación contra `calc_solar_fv_mw`.

## Columnas derivadas

Se calculan después del merge (cruzan tablas), definidas en `bronze_config.DERIVED_COLUMNS`:

- `calc_solar_fv_mw = GREATEST(0, entsoe_solar_mw - esios_gen_ree_gsolter_mw)` — FV limpia, reemplaza a `ree_gsolar_mw` crudo (D-03).
- `calc_hydro_dispatch_mw = entsoe_hydro_reservoir_mw + entsoe_pumping_gen_mw.fillna(0)` — propuesta propia, no es una decisión documentada del equipo.
- `calc_autoconsumo_mw = load_inter_ree_load - load_inter_entsoe_load` — estima el autoconsumo peninsular (D-03); no es una segunda candidata a "demanda real", esa ya la resuelve `entsoe_load`.

## Eje temporal

- Cada tabla renombra su columna de tiempo a `ts_utc` (o `date_local` para tablas diarias).
- Postgres guarda `timestamp with time zone`; el offset exportado es hora local de Madrid, no UTC literal — `pd.to_datetime(..., utc=True)` resuelve el instante real sin ambigüedad.
- El calendario es una dimensión independiente (`build_calendar()`), no una tabla "espina" — cada tabla se cuelga de él según su `grain`. El rango se ancla a `DATASET_START`/`DATASET_END` de `construir_dataset_maestro.py` (importados, no reescritos a mano) — no al rango completo de `ANCHOR_TABLE` ni a la unión de lo cargado.

### Tres granularidades, tres formas de unir (sin imputar en ninguna)

| `grain` | Tablas | Cómo se une | Nulos |
|---|---|---|---|
| `hourly` | entsoe_gen_data, esios_gen, load_inter, esios_forecast_da | Exacto por `ts_utc` | Huecos reales quedan en `NaN` |
| `daily` | commodities | Solo en la hora `00:00` local de cada día — no se difunde a las otras 23 | 23/24 horas en `NaN`, a propósito |
| `3h` | era5_weather_agg | Exacto por `ts_utc`, igual que `hourly` — **no** se sostiene el último valor conocido | Huecos reales quedan en `NaN` |

Mismo criterio en las tres: el bronce no imputa, cualquiera sea la granularidad de origen. Ni `merge_asof` ni difusión de valores — quedó descartado explícitamente después de una primera versión que sí lo hacía.

## Nombres de columna

Prefijo por tabla (`entsoe_*`, `esios_gen_*`, `load_inter_*`, `forecast_*`, `commodities_*`, `era5_*`): columnas que miden cosas distintas en fuentes distintas (ej. solar agregada de ENTSO-E vs. FV pura de ESIOS) no se confunden entre sí.

## Extracción y unión separadas

| Artefacto | Hace | Toca Postgres |
|---|---|---|
| `scripts/extract_bronze.py` | Postgres → parquet normalizado, tabla por tabla (SELECT de columnas configuradas, no `SELECT *`) | Sí |
| `notebooks/01_union_bronze.ipynb` | Lee parquets existentes, arma calendario, merge por `grain`, calcula derivadas | No |
| `scripts/bronze_config.py` | Registro de tablas, columnas, `grain`, derivadas y convención de nombres | — |

Actualizar una tabla no fuerza recalcular el resto ni el merge. `bronze_config.py` es la única fuente de verdad compartida entre los dos scripts — incluida la lista de columnas por tabla, para que no haya drift entre lo que se extrae y lo que la unión espera encontrar.

## EDA — `notebooks/02_eda_bronze.ipynb`

Contrasta cada decisión importante del equipo contra el bronce real, en bloques **contexto → decisión → evidencia → consecuencia**:

- **A.1-A.3**: D-02 (gas) y D-03 (autoconsumo demanda + solar) — confirmadas con evidencia propia, coinciden casi al MW/correlación exacta con lo documentado.
- **A.4**: D-01 y D-04 — pendientes, necesitan `esios_capacity_*` en el bronce.
- **B**: perfil de nulos — dos hallazgos documentados (convención NULL/0 asimétrica en `pumping_gen_mw`/`pumping_cons_mw`; hueco menor sin explicar en `era5_wind_gust10_mean`).
- **C**: outliers — dos hallazgos documentados (`entsoe_load` con 9 horas en cero espurio, afecta `calc_autoconsumo_mw`; generación solar/termosolar negativa nocturna, confirmada como consumo auxiliar físico, no error).
- **D**: cobertura temporal/DST — confirmado, sin anomalías reales.
- **E**: consistencia cruzada eólica/hidráulica — pendiente, necesita columnas de evidencia adicionales.

## Ubicación en el repo

```
scripts/bronze_config.py
scripts/extract_bronze.py
notebooks/01_union_bronze.ipynb
notebooks/02_eda_bronze.ipynb
docs/columnas_bronce_eda.md
data/bronze/*_raw.parquet
data/bronze/bronze_unificado.parquet
```

- No en `ingesta/`: esa carpeta ingesta hacia Postgres; esto lee desde Postgres hacia afuera.
- `scripts/`, `notebooks/` y `docs/` ya existían — sin carpetas nuevas.
- `data/bronze/` separado de `data_temp/` (scratch de descargas): esto es el artefacto a consultar, no basura intermedia.
- Código nunca dentro de `data/bronze/`: esa carpeta se regenera entera con cada corrida.

## Nombres de archivo

- Por tabla: `<nombre_tabla>_raw.parquet`.
- Unificado: `bronze_unificado.parquet` — genérico, no atado a las tablas de hoy.
- Ambos definidos en `bronze_config.py` (`raw_path()`, `UNIFIED_FILENAME`).

## Uso

```bash
python scripts/extract_bronze.py                # todas las tablas
python scripts/extract_bronze.py esios_gen       # una sola
# luego correr notebooks/01_union_bronze.ipynb (working directory: notebooks/)
# y notebooks/02_eda_bronze.ipynb para el contraste de decisiones
```

## Fuera de alcance (silver)

Imputación, outliers, deduplicación, relleno de huecos, Kelvin→Celsius, particionado. El notebook de EDA ya documenta *dónde* hace falta cada uno (ver sección EDA arriba) — la implementación queda para silver.

## Próximos pasos

1. Sumar `esios_capacity_available`, `esios_capacity_installed` y `esios_capacity_available_fc` para cerrar A.4 (D-01, D-04).
2. Columnas de evidencia para E (eólica/hidráulica cruzada), mismo criterio que `gas_mw`/`ree_gsolar_mw`.
3. `esios_pbf_*` cuando el equipo lo priorice (33 días de histórico, sesgo conocido vs. real).
4. `ecmwf_forecast_agg` (3h, mismo patrón que `era5_weather_agg`).