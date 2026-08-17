# Pipeline de bronce — TFM Energía UCM

Capa bronce del pipeline medallón: une tablas fuente sobre un eje temporal común, mínimamente transformadas, lista para EDA. No imputa, no trata outliers, no deduplica — eso es plata.

## Alcance actual

`entsoe_gen_data` + `esios_gen`. Ambas horarias nativas, y juntas permiten contrastar `entsoe_gen_data.solar_mw` (agregada FV+termosolar) contra el par separado de `esios_gen` (`ree_gsolar_mw` + `ree_gsolter_mw`) — pregunta abierta en la auditoría de calidad de datos.

Corrección sobre la marcha: la auditoría ubicaba ese par en `esios_marketdata`; en realidad vive en `esios_gen`. `esios_marketdata` ya no existe — se decidió borrarla de la base.

Resto de tablas (forecast, PBF, capacidad, commodities, meteo) quedan para iteraciones siguientes — se suman de a una, no todas juntas.

## Eje temporal

- Cada tabla renombra su columna de tiempo a `ts_utc`.
- Postgres guarda `timestamp with time zone`; el offset exportado es hora local de Madrid, no UTC literal — `pd.to_datetime(..., utc=True)` resuelve el instante real sin ambigüedad (verificado: `01:00:00+01` → `00:00:00 UTC`).
- El calendario es una dimensión independiente (`build_calendar()`), no una tabla "espina" — cada tabla se cuelga de él por `ts_utc`. Permite sumar tablas diarias o de 3h sin rediseñar el pipeline.
- El rango se ancla a `ANCHOR_TABLE` (`entsoe_gen_data`), no a la unión de lo que esté cargado — el calendario no cambia de tamaño según qué tablas estén disponibles en cada corrida.
- Fuera de esta fase: deduplicación, relleno de huecos, reporte de calidad post-merge.

## Nombres de columna

Prefijo por tabla (`entsoe_*`, `esios_gen_*`): `entsoe_solar_mw` y `esios_gen_ree_gsolar_mw` miden cosas distintas (agregada vs. FV pura), el prefijo lo deja explícito.

## Extracción y unión separadas

| Artefacto | Hace | Toca Postgres |
|---|---|---|
| `scripts/extraccion_bronce.py` | Postgres → parquet normalizado, tabla por tabla | Sí |
| `notebooks/02_union_bronce.ipynb` | Lee parquets existentes, arma calendario, merge | No |
| `scripts/bronce_config.py` | Registro de tablas y convención de nombres | — |

Actualizar una tabla no fuerza recalcular el resto ni el merge. `bronce_config.py` es la única fuente de verdad compartida entre los dos scripts.

## Ubicación en el repo

```
scripts/bronce_config.py
scripts/extraccion_bronce.py
notebooks/02_union_bronce.ipynb
data/bronze/*_raw.parquet
data/bronze/bronce_unificado.parquet
```

- No en `ingesta/`: esa carpeta ingesta hacia Postgres; esto lee desde Postgres hacia afuera.
- `scripts/` y `notebooks/` ya existían — sin carpetas nuevas.
- `data/bronze/` separado de `data_temp/` (scratch de descargas): esto es el artefacto a consultar, no basura intermedia.
- Código nunca dentro de `data/bronze/`: esa carpeta se regenera entera con cada corrida.

## Nombres de archivo

- Por tabla: `<nombre_tabla>_raw.parquet`.
- Unificado: `bronce_unificado.parquet` — genérico, no atado a las tablas de hoy.
- Ambos definidos en `bronce_config.py` (`raw_path()`, `UNIFIED_FILENAME`).

## Uso

```bash
python scripts/extraccion_bronce.py                # todas las tablas
python scripts/extraccion_bronce.py esios_gen       # una sola
# luego correr notebooks/02_union_bronce.ipynb (working directory: notebooks/)
```

## Fuera de alcance (plata)

Imputación, outliers, deduplicación, relleno de huecos, difusión de tablas diarias sobre 24h, Kelvin→Celsius, reporte de calidad, particionado.

## Próximos pasos

1. `esios_load_inter` + `entsoe_load_inter`
2. `esios_forecast_da`
3. `esios_pbf_*` (33 días de histórico, sesgo conocido vs. real)
4. `commodities`, `esios_capacity_*` (ejercita `date_local`)
5. `era5_weather_agg`, `ecmwf_forecast_agg` (3h, backfill 2020-2024)
