# Decisiones tomadas

Registro de las decisiones cerradas sobre el dataset de modelado. Una por sesión,
con la evidencia que la respalda, para no volver a discutirlas y para poder citarlas
en la memoria.

---

## D-01 · `esios_capacity_available` sí es una feature legítima, pero hay que capturarla de otra forma

**Fecha:** 18 de agosto de 2026
**Estado:** cerrada
**Afecta a:** 8 columnas de `esios_capacity_available` · nueva tabla `esios_capacity_available_fc`

### La pregunta

La potencia disponible es de las variables que mejor explican los picos de precio: se
calcula como potencia instalada menos las indisponibilidades declaradas por los sujetos
del mercado, así que capta las paradas de nucleares y los mantenimientos. Una nuclear en
recarga quita alrededor de un gigavatio del sistema.

Pero para usarla como *feature* hace falta saber si está disponible **antes de las 12:00
del día D-1**, que es cuando cierra el mercado diario. Si se publica después, usarla sería
fuga de información.

### La evidencia

La ficha oficial de ESIOS, idéntica para los ocho indicadores (472 a 479):

> *"Calculada, para cada tipo de producción de generación convencional, como diferencia
> entre la suma de potencias netas instaladas registradas en el sistema e·sios y el total
> de la potencia indisponible declarada por los Sujetos del Mercado. (…)
> **Publicación: diariamente a partir de las 4:30 horas con los datos por día y hora del
> trimestre actual y el siguiente.**"*

Fuente: `ingesta/check_tables/indicators.xlsx`, catálogo oficial de los 1.966 indicadores.

Dos conclusiones:

1. **Se publica a las 4:30**, siete horas y media antes del cierre del mercado.
2. **El horizonte es el trimestre actual y el siguiente**, no solo D+1. A las 4:30 de hoy
   ya se conoce la disponibilidad declarada para mañana, para la semana que viene y para
   dentro de dos meses.

Como fuente, por tanto, es válida y además muy potente.

### El problema: no es la fuente, es la ingesta

`esios_daily_capacity_available.py` funciona así:

- Cron a las 21:05.
- Revisa los últimos 30 días y, para cada fecha que no esté en la base, pide a la API
  **solo esa fecha** (`start_date` y `end_date` son el mismo día).
- Guarda una fila con clave `date`.
- `REVISAR_EXISTENTES = False`, así que una fecha ya cargada no se vuelve a tocar.

Consecuencia: la fila del día X se crea el día X a las 21:05 y contiene *lo que se sabía
del día X al final del día X*. **El horizonte hacia adelante se descarta**, que es justo
lo que hacía valiosa a esta serie.

### La trampa, y por qué importa

Si alguien usara la tabla tal cual como feature, estaría metiendo el valor registrado a
las 21:05 del mismo día que quiere predecir. La mayor parte del tiempo ese valor se
parecería al que estaba declarado la víspera —las indisponibilidades programadas se
anuncian con semanas de antelación— así que el error pasaría desapercibido.

Salvo cuando hay **una parada imprevista**. Que es exactamente el caso que más mueve el
precio. La fuga de información se concentraría en los eventos de mayor impacto, y el
modelo parecería predecir muy bien precisamente los días que no puede predecir.

### La decisión

Crear **`esios_capacity_available_fc`**, con clave `(run_date, target_date)`, capturando
cada mañana el horizonte completo. Guardar la fecha de consulta permite hacer la pregunta
correcta: *"el día D, ¿qué disponibilidad había declarada para D+1?"*.

| | Tabla actual | Tabla nueva |
|---|---|---|
| Clave | `date` | `(run_date, target_date)` |
| Cron | 21:05 | 05:30 Madrid (tras la publicación de las 4:30) |
| Qué guarda | El valor del propio día | El horizonte de 45 días desde la consulta |
| Uso | Solo con lag | Feature directa, sin fuga |
| Histórico | Completo desde 2020 | Empieza el día que se active |

**Veredicto para las 8 columnas de la tabla actual:** `SOLO CON LAG`, hasta que la serie
nueva tenga recorrido suficiente.

### Limitación que hay que asumir y documentar en la memoria

**El pasado no se puede reconstruir.** La API devuelve el valor vigente para una fecha, no
el que estaba declarado hace seis meses. La serie anticipatoria empieza el día que se
ponga en marcha el script.

No es un defecto del diseño: es una limitación de la fuente, y es el mismo caso que
`ecmwf_forecast_load.py`, que tampoco puede recuperar pronósticos pasados. Conviene
mencionarlo en la memoria como una restricción conocida y gestionada, no como un olvido.

Consecuencia práctica:

- Modelos entrenados sobre el histórico 2020-2026 → tabla antigua, con lag.
- A partir de la puesta en marcha → se acumula la serie buena, utilizable sin lag.

### Regalo inesperado

Al guardar la fecha de consulta aparece una feature que **no existe en ninguna otra tabla
del proyecto**: la *revisión* de la disponibilidad entre capturas.

Si la nuclear declarada para el día 20 baja 1.000 MW entre la captura del día 13 y la del
día 19, eso es una parada que acaba de anunciarse. Es una señal de cambio, no de nivel, y
llega antes de que el mercado la haya digerido del todo.

```sql
SELECT a.target_date,
       a.nuclear_mw - b.nuclear_mw AS revision_nuclear_7d
FROM esios_capacity_available_fc a
JOIN esios_capacity_available_fc b
  ON b.target_date = a.target_date
 AND b.run_date    = a.run_date - INTERVAL '6 days'
WHERE a.run_date = a.target_date - INTERVAL '1 day';
```

### Implementación — hecha el 19 de agosto

- ✅ `esios_capacity_available_fc.py` subido a la rama `magui_test` (commit `8c2dc13`).
- ✅ Desplegado en el servidor en `~/scripts/ingesta/`.
- ✅ Primera captura ejecutada: **45 filas**, horizonte del 19-ago al 2-oct, D+1 capturado.
- ✅ Cron añadido a las 5:30 (hereda el `CRON_TZ=Europe/Madrid` que ya tenía el crontab).
- ✅ Excel actualizado: las columnas de `esios_capacity_available` pasan a `SOLO CON LAG`
  y se añade `esios_capacity_available_fc` como `MANTENER`.

**La hipótesis quedó confirmada contra la API real, no solo contra la documentación.**
La primera ejecución devolvió 45 días hacia adelante, y los valores **varían dentro del
horizonte**: la nuclear declarada pasa de 7.029 MW el día 19 a 7.117 MW el día 20. Si
fuera un valor plano propagado no habría nada que capturar; que cambie confirma que la
ventana futura lleva información real.

Nota menor: `coal_subbituminosa_mw` (indicador 476) devuelve cero días con dato, igual que
en la tabla antigua. Es una serie que ESIOS no publica, no un fallo de carga.

### Qué queda por hacer

1. Comprobar el 20 de agosto que el cron se disparó: `tail -20 ~/scripts/logs/cron_capacity_fc.log`.
2. Con tres o cuatro capturas acumuladas, verificar que el D+1 entra de forma consistente.
3. Cuando haya suficiente serie, incorporar el desglose por tecnología —sobre todo la
   nuclear— al dataset maestro, que hoy solo usa `total_mw` de la tabla antigua.

---

## D-02 · El gas de ENTSO-E y el de ESIOS no son intercambiables

**Fecha:** 19 de agosto de 2026
**Estado:** cerrada
**Afecta a:** `entsoe_gen_data.gas_mw` · `esios_gen.ree_gccgas_mw` · `esios_gen.ree_gotherthermal_mw`
**Evidencia:** `ingesta/_tests/TEST_701_gas_esios_vs_entsoe.py` · 58.067 horas, 2020 a agosto de 2026

### La pregunta

El docstring de `entsoe_daily_pipeline.py` afirma que el código PSR B04 *"incluye la
cogeneración de gas, que en España no tiene código PSR propio según el Anexo II del
P.O. 3.1"*. En ESIOS ese mismo perímetro está partido en dos indicadores:

| | Indicador | Qué cubre |
|---|---|---|
| `ree_gccgas_mw` | 550 | Ciclo combinado, **sin** cogeneración |
| `ree_gotherthermal_mw` | 1297 | Cogeneración y resto térmico |

Si la suma de los dos reproduce el B04, las dos fuentes son intercambiables y basta con
elegir una. Si no, cada columna mide un perímetro distinto y hay que decidir cuál entra
al dataset.

### La evidencia

Sobre 58.067 horas comparables, sin un solo nulo en `ree_gotherthermal_mw`:

| Comparación | Correlación |
|---|---|
| `entsoe_gas` vs `ree_gccgas_mw` | 0,97881 |
| `entsoe_gas` vs `ree_gccgas + ree_gotherthermal` | **0,99626** |

| Diferencia | Media | Mediana | Desviación | p05 | p95 |
|---|---|---|---|---|---|
| `entsoe_gas − ccgt` | 1.893,9 | 1.840,7 | 657,9 | 886,7 | 2.875,2 |
| `entsoe_gas − (ccgt + otras)` | −374,6 | −309,5 | 282,1 | −882,2 | −27,4 |

**La primera conclusión es firme:** el B04 equivale a la suma, no al ciclo combinado solo.
Confundirlos supone un error de unos **1.900 MW de media**, que sobre una generación de gas
que ronda los 6.000 MW es un tercio de la variable.

### Por qué la diferencia no es un offset estable

Éste era el punto que decidía si las series se podían convertir una en otra. No se puede,
y el motivo es más interesante de lo que parecía:

| Año | `entsoe_gas` | `esios_ccgt` | `esios_otras` | Diferencia |
|---|---|---|---|---|
| 2020 | 7.010 | 4.416 | **3.400** | 2.593 |
| 2021 | 7.035 | 4.358 | **3.235** | 2.677 |
| 2022 | 8.818 | 6.963 | **2.196** | 1.854 |
| 2023 | 6.209 | 4.546 | **1.920** | 1.663 |
| 2024 | 4.830 | 3.314 | **1.738** | 1.516 |
| 2025 | 5.950 | 4.426 | **1.701** | 1.524 |
| 2026 | 5.607 | 4.456 | **1.341** | 1.151 |

El offset varía **1.526 MW** entre el primer año y el último. No es ruido: es que
**la cogeneración española ha caído un 60 % en seis años**, de 3.400 MW a 1.341 MW.

Eso convierte lo que parecía un detalle de fontanería en un hecho del sistema que merece
mención en la memoria: es un cambio estructural del mismo orden que el cierre del carbón
en 2021, y ninguna de las dos fuentes lo señala por sí sola. Solo aparece al compararlas.

El salto de 2022 tiene además su propia explicación: durante la crisis del gas el ciclo
combinado se disparó a 6.963 MW mientras la cogeneración seguía cayendo.

### El residuo negativo, y qué significa

La diferencia contra la suma es pequeña pero **sistemáticamente negativa**: la suma de
ESIOS supera al B04. Y va encogiendo: −806 MW en 2020, −190 MW en 2026.

La explicación es que `ree_gotherthermal_mw` no es solo cogeneración de gas: incluye
cogeneración de otros combustibles y resto térmico. Ese exceso es justo lo que sobra al
compararlo con un indicador que solo mide gas. Y se encoge porque esa cogeneración no-gas
también está desapareciendo.

Dicho de otro modo: la suma de ESIOS **sobreestima el gas** en unos 190 MW recientes.
Poco, pero conviene saberlo antes de usarla como si fuera gas puro.

### La decisión

**Usar ESIOS, con las dos columnas separadas.** No es preferencia de fuente, es un motivo
físico:

- El **ciclo combinado responde al precio**: entra cuando hace falta y marca el marginal.
- La **cogeneración es prácticamente inelástica**: funciona por proceso industrial, no por
  señal de mercado.

Meterlas juntas en una sola columna, como hace el B04, diluye exactamente la señal que
interesa para predecir el precio. Y una vez agregadas por ENTSO-E, no hay forma de
separarlas.

`entsoe_gen_data.gas_mw` pasa a **DESCARTAR**. `ree_gccgas_mw` y `ree_gotherthermal_mw`
se mantienen, con lag, como dos variables distintas.

### Consecuencia que va más allá del gas

Que la relación entre las dos fuentes cambie de régimen en 2022 significa que **no se
puede cambiar de fuente a mitad del análisis**. Un modelo entrenado con una y evaluado con
otra mediría mal, y con el test puesto en 2026 el error caería justo en el periodo de
evaluación.

Es el mismo problema de fondo que la contradicción pendiente entre `ree_load` y
`entsoe_load` (decisión nº 10 del Excel). Conviene tratarlas juntas: **fijar la fuente de
cada magnitud y no tocarla**.

### Hallazgo colateral: el gas no estaba en el dataset

Al comprobar el punto anterior salió algo que no buscábamos. `construir_dataset_maestro.py`
lleva lags de datos reales de tres magnitudes —demanda, eólica con bombeo, y solar— y
**ninguna columna de generación de gas**, ni de ESIOS ni de ENTSO-E.

El *precio* del gas sí está, vía `commodities`. Pero no es lo mismo: el precio dice cuánto
cuesta, y la generación de ciclo combinado dice si el gas **viene marcando el marginal**.
En las horas sin renovable, esa es la tecnología que fija el precio.

Así que D-02 no solo dice qué columna descartar: dice cuál añadir. Y ahora sabemos que es
`ree_gccgas_mw`, no `entsoe_gen_data.gas_mw`.

### Implementación

- ✅ `TEST_701_gas_esios_vs_entsoe.py` subido a `ingesta/_tests/` desde `magui_test`.
- ✅ Verificado que `entsoe_gen_data.gas_mw` no entra en el dataset maestro: la decisión
  es compatible con lo que ya está construido, no hay que deshacer nada.
- ✅ Excel actualizado: `entsoe_gen_data.gas_mw` pasa a `DESCARTAR`; `ree_gccgas_mw` y
  `ree_gotherthermal_mw` se mantienen como dos variables distintas.
- 🕐 Parche preparado y probado (`gas_lags.patch`) para añadir `ree_gccgas_mw` a los lags
  de D-1 y D-7. Son seis columnas nuevas. **Pendiente del visto bueno del equipo**, porque
  toca un fichero compartido que todos usan para modelar.

### Qué queda por hacer

1. Aplicar `gas_lags.patch` cuando el equipo dé el visto bueno.
2. Añadir la caída de la cogeneración al apartado de contexto del sistema en la memoria.

---

*Siguiente decisión pendiente: las zonas horarias mezcladas entre tablas, y la
contradicción `ree_load` / `entsoe_load` (las dos bloqueantes).*
