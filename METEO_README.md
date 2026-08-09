# METEO — Memoria técnica del pipeline meteorológico

Documento de referencia para `era5_load.py` (histórico, CDS) y
`ecmwf_forecast_load.py` (pronóstico D+1, ECMWF Open Data). Cubre setup,
decisiones de diseño, significado de cada variable, y problemas ya
resueltos para no volver a perder tiempo con ellos.

---

## 1. Setup (Copernicus CDS)

**Lo normal: no hace falta hacer nada de esto.** El `credentials.json` del
equipo ya trae un `cds_api_key` funcional (cuenta de Leandro) — cualquiera
puede correr `era5_load.py` tal cual, reusando esa key, sin crear cuenta
propia.

**Seguir estos pasos solo si:**
- la key falla (token revocado, expirado, o error de autenticación), o
- se quiere una cuenta propia para no compartir cola/cuota con el resto
  (relevante si vas a correr cargas grandes en paralelo con otra persona —
  ver nota de cuotas en la sección 4).

1. Cuenta gratis en https://cds.climate.copernicus.eu
2. Token en tu perfil: https://cds.climate.copernicus.eu/profile (un solo
   Personal Access Token — el sistema nuevo ya no usa UID:key).
3. Aceptar la licencia del dataset (obligatorio, manual, una vez):
   https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels
   → sección "Terms of Use".
4. Reemplazar en tu `credentials.json` local (en `ingesta/`, nunca se sube
   a git) el campo:
   ```json
   "cds_api_key": "TU_TOKEN_PROPIO"
   ```
5. Dependencias:
   ```bash
   pip install cdsapi xarray netCDF4 psycopg2-binary numpy
   ```
6. Prueba con un mes:
   ```bash
   python era5_load.py --start 2026-06 --end 2026-06
   ```

---

## 1b. Setup (ECMWF Open Data — para `ecmwf_forecast_load.py`)

**No hace falta cuenta ni token.** Open Data es de acceso libre — a
diferencia de CDS, no requiere `credentials.json` ni ningún campo nuevo.

Dependencias (distintas a las de la sección 1, hay que instalarlas aparte):
```bash
pip install ecmwf-opendata cfgrib
```

**Usar `pip`, no `conda install -c conda-forge`, para estas dos.** El
solver de conda en el entorno `base` puede quedarse colgado en
`Solving environment` varios minutos (mismo problema que ya vimos con
`psycopg2`) — reconciliar canales cuando `base` ya tiene muchos paquetes es
lento. `pip install` no tiene ese problema aquí.

Prueba:
```bash
python ecmwf_forecast_load.py
```
(usa la fecha de hoy por defecto — descarga el pronóstico D+1 del run 00Z).

---

## 2. Decisiones de diseño

**Área geográfica — España peninsular + Baleares**
`{"north": 44, "west": -9.5, "south": 36, "east": 4.5}`. Se excluye
Canarias (no interconectada al sistema peninsular) y Francia/Portugal (esa
señal ya la aporta ENTSO-E vía flujos de interconexión). Mismo bounding box
en ambos loaders — imprescindible para que el tensor histórico y el de
pronóstico sean compatibles en shape.

**Dos fuentes, dos roles — no son intercambiables**
- `era5_load.py` → ERA5 (reanálisis CDS): histórico/entrenamiento. Reanálisis
  = mejor reconstrucción de lo que realmente pasó (asimila observaciones).
  Es un **archivo profundo**: cualquier fecha pasada sigue disponible siempre.
- `ecmwf_forecast_load.py` → ECMWF Open Data (IFS): producción D+1 y,
  acumulando días, validación de cuánto degrada el modelo la incertidumbre
  de pronóstico. Es una **ventana móvil muy corta** (~4 días observados en
  data.ecmwf.int) — no tiene archivo histórico. Si un día no se descarga
  dentro de esa ventana, se pierde para siempre; no hay forma de "rellenar"
  un D+1 de hace 2 semanas después del hecho.

  Por eso `ecmwf_forecast_load.py` **no** usa el patrón `--start`/`--end` de
  meses atrasados como `era5_load.py` — su trabajo es capturar el D+1 de
  *hoy*, todos los días, en tiempo real (no "ponerse al día con el pasado",
  sino "no dejar escapar el presente"). Para tolerar fallos puntuales del
  cron, `main()` corre el run de `--run-date` (hoy por defecto) y además
  `revisar_dias_recientes()`, que reintenta los últimos `--dias-revision`
  (4 por defecto) por si algún día quedó incompleto y Open Data todavía lo
  tiene disponible — mismo patrón que `entsoe_daily_pipeline.py`
  (`DIAS_REVISION`), adaptado a esta ventana mucho más corta.

  Un hueco temporal entre "hasta dónde llega ERA5" y "hoy" **no es un
  problema de estos dos loaders** — son datasets que nunca se concatenan en
  un mismo archivo, cada uno alimenta una etapa distinta del pipeline.

**Ventana de contexto de la RNN — pregunta de diseño abierta, no de los loaders**
El planteamiento del TFM incluye una RNN para predecir el precio D+1, que
normalmente necesita una ventana de contexto reciente (p. ej. últimas 24-48h
de condiciones reales) además del pronóstico del día siguiente. El problema:
ni ERA5 (ERA5T preliminar tiene ~5 días de retraso, demasiado lento para
"ayer") ni Open Data (solo mira hacia adelante desde el run) cubren esa
ventana de pasado reciente en producción. Soluciones típicas en pipelines
operativos similares:
1. Usar el pronóstico D+1 **de ayer** (que ya "pasó") como proxy de las
   condiciones de hoy, encadenando pronósticos pasados en vez de reanálisis.
2. Reducir la ventana de contexto de la RNN a algo que sí pueda cubrirse
   solo con pronóstico (sin depender de reanálisis reciente).

Pendiente de decidir al diseñar la arquitectura de F12/F13 — no bloquea el
trabajo de los loaders, pero condiciona qué inputs van a estar realmente
disponibles el día que el modelo corra en producción.

**`msl` en vez de `sp`**
`surface_pressure` mezcla efecto de altitud (Madrid vs. costa) con efecto
meteorológico. `mean_sea_level_pressure` corrige por altitud — es el
estándar para detectar regímenes sinópticos (anticiclón ibérico = calma,
poco viento, mucho sol; borrasca = lo contrario). Se usa `msl` en ambos
loaders.

**Tabla + tensor, no uno u otro**
`era5_weather_agg` (Postgres) = media espacial por hora, para EDA de
tendencia/calidad de datos (huecos, outliers, estacionalidad). El tensor
`.npy` = malla espacial completa por mes, para el CNN (F12) y para
cualquier análisis espacial que la tabla no puede responder (la media
colapsa la posición: "niebla norte + sol sur" y "sol parejo en todo el
país" pueden dar la misma media). El tensor es la fuente de verdad; la
tabla es una capa de conveniencia derivada de él.

**`tensor_index`**
Columna que guarda la posición exacta de esa hora dentro del array del
`.npy` mensual (`tensor[tensor_index]`), para no recalcular fecha→índice
en cada consulta al construir el dataset de entrenamiento del CNN.

**Ruta del tensor — relativa, no absoluta**
`TENSOR_OUTPUT_DIR = Path(__file__).parent / "tensors" / "era5"` (o el
equivalente en `ecmwf_forecast_load.py`). Relativa al script, no a un disco
de una máquina concreta — funciona igual para cualquiera que clone el
repo. La carpeta `tensors/` va en `.gitignore` (son binarios pesados y
regenerables, no código).

---

## 3. Variables ingeridas — glosario

| Variable | Unidad | Qué es | Por qué importa para el precio |
|---|---|---|---|
| `t2m` | K | Temperatura a 2m | Demanda (calefacción/refrigeración) |
| `d2m` | K | Punto de rocío a 2m | Humedad → confort térmico, complementa `t2m` en demanda de climatización |
| `u10`, `v10` | m/s | Componentes de viento a 10m | Base para `wind10` (velocidad); proxy de superficie |
| `u100`, `v100` | m/s | Componentes de viento a 100m | Base para `wind100` — altura real de buje eólico, mejor proxy de generación |
| `wind_gust10` | m/s | Ráfaga instantánea a 10m | Rachas extremas → autoapagado de aerogeneradores (curtailment), afecta oferta eólica de forma no lineal |
| `ssrd` | W/m² | Radiación solar incidente real | Generación solar directa |
| `ssrdc` | W/m² | Radiación solar en cielo despejado (teórico) | `ssrd/ssrdc` = índice real de nubosidad, mejor predictor solar que `tcc` solo |
| `tcc` | fracción 0-1 | Nubosidad total | Atenuación solar |
| `tp` | mm | Precipitación acumulada en la hora | Proxy de aportes hidráulicos (embalses) |
| `msl` | Pa | Presión a nivel del mar | Régimen sinóptico (anticiclón/borrasca) — ver sección 2 |

`wind10`/`wind100` (las que realmente se guardan en la tabla) se calculan
como módulo del vector: `√(u² + v²)`. El tensor guarda `u`/`v` por
separado (conserva dirección); la tabla solo guarda el módulo.

Variables descartadas del catálogo completo de ERA5 (~250 disponibles):
todo lo de oleaje (`mean_wave_*`, energía marina), variables oceánicas,
lagos, vegetación, nieve en capas, integrales verticales de flujo — sin
relación con el sistema eléctrico peninsular.

---

## 4. Problemas ya resueltos (para no repetirlos)

- **CDS API nueva sintaxis**: `"data_format": "netcdf"` (no `"format"`),
  `"download_format": "unarchived"`, y `product_type`/`year`/`month` como
  **listas**, no strings sueltos.
- **CDS devuelve ZIP aunque pidas `unarchived`**: pasa cuando se mezclan
  variables acumuladas (`ssrd`, `ssrdc`, `tp`) con instantáneas (el resto)
  — las separa en dos NetCDF (`...-accum.nc` / `...-instant.nc`) y las
  empaqueta en ZIP. `open_era5_dataset()` lo detecta con
  `zipfile.is_zipfile()` (sin fiarse de la extensión) y los une.
- **`WinError 32` al borrar el temp dir (solo Windows)**: xarray mantiene
  el archivo abierto aunque llames `.load()`. Fix: abrir cada NetCDF con
  `with xr.open_dataset(f) as ...` para forzar el cierre antes de que se
  borre la carpeta temporal.
- **`valid_time` en vez de `time`, dims `number`/`expver` extra**: formato
  nuevo de CDS. `_normalize_dims()` colapsa `number` (ensemble, size 1),
  combina `expver` (prioriza ERA5 final sobre ERA5T preliminar donde
  ambas existan, con `combine_first`), y renombra `valid_time`→`time`.
- **Nombre de la variable de ráfaga varía**: `i10fg` o `fg10` según
  versión del conversor — `GUST_VAR_CANDIDATES` prueba ambos.
- **Conexión a Postgres se corta en esperas largas** (la cola de CDS puede
  tardar 20-40 min): `load_month()` revisa `conn.closed` y reconecta antes
  de cada mes; toda la lógica queda dentro de un único `try/except` para
  que un mes fallido no tumbe el resto del rango.
- **Esquema de tabla evoluciona con el tiempo** (se añadieron `d2m`, `tp`,
  `ssrdc`, `wind_gust10`, se renombró `sp`→`msl`): `ensure_table()` usa
  `ALTER TABLE ADD COLUMN IF NOT EXISTS` para migrar tablas ya existentes,
  no solo `CREATE TABLE IF NOT EXISTS`. Columna vieja huérfana (`sp_mean`)
  se elimina a mano: `ALTER TABLE era5_weather_agg DROP COLUMN sp_mean;`
- **Cortes de red en descargas largas**: la propia librería `cdsapi` ya
  reintenta sola (hasta 500 intentos, cada 120s) — no es un bug nuestro,
  pero conviene desactivar la suspensión automática de Windows si se deja
  corriendo desatendido varias horas.

---

## 5. Consultas SQL útiles

```sql
-- Meses/años distintos cargados, con conteo de horas por mes
SELECT DATE_TRUNC('month', ts)::date AS mes, COUNT(*) AS horas_cargadas
FROM era5_weather_agg GROUP BY 1 ORDER BY 1;

-- Resumen general
SELECT COUNT(DISTINCT DATE_TRUNC('year', ts))  AS anios_distintos,
       COUNT(DISTINCT DATE_TRUNC('month', ts)) AS meses_distintos,
       MIN(ts) AS primera_hora, MAX(ts) AS ultima_hora
FROM era5_weather_agg;
```

---

## 6. Pendientes abiertos

- Confirmar disponibilidad real de `ssrdc` y el código de ráfaga en
  ECMWF Open Data (puede no estar en su lista limitada de parámetros;
  `ecmwf_forecast_load.py` ya maneja el caso con `NaN` si falta).
- Decidir ruta de `tensor_dir` cuando esto se despliegue en el servidor
  (disco dedicado vs. relativa al repo).
- Validar `ecmwf_forecast_load.py` contra una descarga real (por ahora
  solo probado con datos sintéticos).
- Resolver la ventana de contexto reciente de la RNN (ver sección 2) antes
  de diseñar el input de F12/F13.

~~Revisión de últimos días si falla el cron~~ — resuelto:
`revisar_dias_recientes()` / `--dias-revision` en `ecmwf_forecast_load.py`.