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

## D-03 · Ninguna serie de ESIOS con autoconsumo dentro es homogénea desde diciembre de 2025

**Fecha:** 19 de agosto de 2026
**Estado:** cerrada
**Afecta a:** `ree_load` · `ree_gsolar_mw` · y potencialmente a cualquier otra serie de ESIOS que incorpore autoconsumo
**Evidencia:** `ingesta/_tests/TEST_702_efecto_autoconsumo_esios.py` · 58.127 horas, 2020 a agosto de 2026

### La pregunta

Dos afirmaciones independientes, hechas por dos personas distintas y sobre columnas
distintas, describían el mismo fenómeno:

> `load_inter_pipeline.py` (18-ago): *"ree_load incorpora la estimación de autoconsumo
> desde dic-2025 y deja de ser homogénea."*

> `matriz_generacion_esios_entsoe.xlsx` (19-ago): *"ESIOS separa [la solar] pero su FV
> incorpora autoconsumo desde dic-2025."*

Si las dos eran ciertas, el problema no era de una columna: era un criterio general.
Y había una contradicción abierta, porque `construir_dataset_maestro.py` usaba las dos
versiones afectadas — `ree_load` para la demanda y `ree_gsolar_mw` para la solar.

### La evidencia

Antes de diciembre de 2025, las series de ESIOS y ENTSO-E son la misma cosa:

| | Correlación | Diferencia media | Mediana |
|---|---|---|---|
| Demanda | 0,99897 | −6,7 MW | −1,7 MW |
| Solar FV | 0,99762 | +0,3 MW | −0,7 MW |

Sobre una demanda que ronda los 28.000 MW, eso es ruido de redondeo.

Desde diciembre, se separan — y **con la misma magnitud, mes a mes**:

| Mes | Diferencia en demanda | Diferencia en solar FV |
|---|---|---|
| nov-2025 | 1,3 | 6,7 |
| **dic-2025** | **434,7** | **435,7** |
| ene-2026 | 685,7 | 687,5 |
| feb-2026 | 1.038,1 | 1.043,5 |
| mar-2026 | 1.599,7 | 1.500,2 |
| abr-2026 | 1.806,9 | 1.824,1 |
| may-2026 | 2.002,3 | 2.025,5 |
| jun-2026 | 2.200,6 | 2.106,0 |
| jul-2026 | 2.495,4 | 2.194,3 |
| ago-2026 | 2.112,6 | 2.105,9 |

El salto total es de **1.582 MW en la demanda y 1.519 MW en la solar**. Prácticamente
el mismo número, y las dos series arrancan el mismo mes.

### Por qué las dos suben a la vez, y en la misma cantidad

No es casualidad ni un doble error: es **coherencia contable**. El autoconsumo se genera
y se consume en el mismo punto, así que al incorporarlo entra por los dos lados del
balance. Si REE estima 2.100 MW de autoconsumo, los suma a la generación fotovoltaica
*y* a la demanda, porque esa energía efectivamente se produce y efectivamente se consume.

Que las dos diferencias coincidan mes a mes es, de hecho, la mejor prueba de que la
explicación es correcta. Dos errores independientes no darían la misma cifra.

### Es autoconsumo fotovoltaico, sin lugar a dudas

El perfil horario desde el corte es una campana solar de manual:

| | Noche (23-04h) | Mediodía (11-15h) |
|---|---|---|
| Diferencia en demanda | 117,3 MW | 3.933,8 MW |
| Diferencia en solar FV | 2,1 MW | 3.894,7 MW |

Y la correlación con la generación fotovoltaica real es de **0,82** para la demanda y
**0,91** para la solar.

Un detalle menor que merece anotarse: la diferencia de demanda nocturna no es exactamente
cero, sino de 100 a 170 MW. Podría ser autoconsumo no solar —cogeneración industrial,
baterías domésticas— o simplemente ruido del método de estimación de REE. No afecta a la
decisión, pero conviene no afirmar que el autoconsumo es *solo* fotovoltaico.

### El argumento que cierra la discusión

| Tramo del split | Horas | Afectadas por el cambio |
|---|---|---|
| train (2020 → 2024) | 43.848 | **0,0 %** |
| validation (2025) | 8.760 | 8,5 % |
| test (2026 →) | 5.519 | **100,0 %** |

El modelo aprendería con datos donde el autoconsumo no existe y se evaluaría con datos
donde vale más de 2.000 MW de media. **No es una preferencia de fuente: es que la
variable no significa lo mismo en entrenamiento y en test.**

Y no es siquiera un escalón que el modelo pudiera aprender: es una rampa que va de 435 MW
en diciembre a 2.500 MW en julio, porque REE incorpora el autoconsumo de forma progresiva.

### La decisión

**Ninguna serie de ESIOS que incorpore autoconsumo puede usarse como si fuera homogénea
en el rango 2020-2026.** En concreto, sobre `construir_dataset_maestro.py`:

| Ahora | Debe pasar a |
|---|---|
| `COLS_DEMANDA_REAL = ["ree_load"]` | la demanda de ENTSO-E |
| `COLS_SOLAR_REAL = ["ree_gsolar_mw", "ree_gsolter_mw"]` | FV derivada: `GREATEST(0, entsoe.solar_mw − ree_gsolter_mw)` + `ree_gsolter_mw` |

La termosolar (`ree_gsolter_mw`) **no está afectada**: su parque está congelado y no tiene
autoconsumo, así que sigue siendo la fuente buena para separar la FV del B16 de ENTSO-E.

Las versiones de ESIOS se conservan como columnas documentales. Su diferencia con las de
ENTSO-E **estima el autoconsumo peninsular**, una magnitud que ninguna fuente publica por
separado: unos 2.100 MW de media en agosto de 2026, con picos de 4.000 MW a mediodía.

### Validación cruzada (añadida el 19-ago, desde la decisión D-04)

Al investigar otra cosa apareció una comprobación independiente de esta estimación.

`esios_capacity_installed.autoconsume_solar_pv_mw` —la **potencia instalada** de
autoconsumo fotovoltaico declarada por REE— vale **9.100 MW en agosto de 2026**, y viene
creciendo desde los 25 MW de enero de 2020.

Nuestra estimación de **generación** media de autoconsumo en ese mismo mes era de 2.100 MW.
Sobre 9.100 MW instalados, eso da un **factor de capacidad del 23 %**, que es exactamente
el rango esperable para fotovoltaica en España.

Es una validación por una vía completamente distinta: la estimación salió de restar dos
series de demanda horaria, y el contraste viene de una tabla de potencia instalada mensual
que no interviene en aquel cálculo. Que el cociente sea físicamente razonable refuerza
bastante el resultado, y conviene mencionarlo en la memoria: convierte «la diferencia entre
dos fuentes» en «la diferencia entre dos fuentes, contrastada contra la potencia instalada».

### Hallazgo colateral: el dataset maestro no se puede ejecutar

Al preparar el test apareció que **`esios_load_inter` ya no existe** en la base de datos:
la sustituyó `load_inter` el 18 de agosto. Y `construir_dataset_maestro.py` la lee en dos
sitios —la demanda real y las NTC— así que **ahora mismo falla al ejecutarse**.

Eso convierte la decisión nº 11 del Excel de "conviene hacerlo" a "hay que hacerlo ya", y
conviene avisar antes de que alguien pierda una tarde con el error.

### Qué queda por hacer

1. Avisar al equipo de que el dataset maestro está roto por el cambio de nombre de tabla.
2. Actualizar `construir_dataset_maestro.py`: tabla `load_inter`, demanda de ENTSO-E y
   FV derivada.
3. Añadir el autoconsumo al apartado de contexto de la memoria. Es un cambio regulatorio
   con efecto medible sobre los datos, y tenemos una estimación propia de su magnitud.

---

## D-04 · Lo que sobra en la base, y una tabla menos informativa de lo que parece

**Fecha:** 19 de agosto de 2026
**Estado:** cerrada
**Afecta a:** `trayport_daily` · `esios_capacity_available_fc` · `esios_capacity_installed`
**Evidencia:** `ingesta/check_tables/revision_huerfanos.py`

### La pregunta

Arrastrábamos dos candidatos a borrar —`trayport_daily` y una columna huérfana en
`ecmwf_forecast_agg`— identificados de oídas, sin comprobar. La pregunta era si de verdad
sobraban, y si había más que nadie hubiera mirado.

El criterio elegido: una columna solo se considera huérfana si está vacía **y además** no
aparece en ningún fichero del repositorio. La segunda condición es la que importa. Una
columna vacía puede estar esperando a que alguien active su pipeline; si nadie la nombra
en el código, no espera nada.

### Lo que se buscaba

**`trayport_daily`** sigue en la base con 41 filas y **22 menciones en el código**, así que
por el criterio estricto no es huérfana. Pero los datos dicen otra cosa: dejó de recibir
filas el 16 de agosto, mientras `trayport_trades` y `trayport_daily_ohlc` siguen hasta el
17. El cron de las 8:00 continúa activo, de modo que el pipeline se reorientó a las tablas
nuevas y ésta quedó abandonada de hecho.

→ **Proponer su borrado**, después de revisar que esas 22 menciones son el propio pipeline
y el comentario de `bronzeDF_pipeline.py` que ya la da por descartada, y no un uso real.

**La columna huérfana de `ecmwf_forecast_agg` ya no existe**: alguien la limpió. Lo único
que aparece al 100 % NULL es `coal_subbituminosa_mw`, y está en
`esios_capacity_available_fc` — la tabla que creamos ayer. Las 8 menciones son de nuestro
propio script.

→ **Retirar el indicador 476 del script.** No es un fallo: ESIOS no publica esa serie, cosa
que ya constaba de la tabla anterior. Pero gasta una llamada a la API cada mañana para
traer nada.

### Lo que no se buscaba, y es lo interesante

El bloque de columnas constantes destapó seis series que no varían en seis años y medio:

| Tabla | Columna | Valor | Filas |
|---|---|---|---|
| `esios_capacity_installed` | `ccgt_mw` | 24.561,85 | 2.422 |
| `esios_capacity_installed` | `nuclear_mw` | 7.117,29 | 2.422 |
| `esios_capacity_installed` | `pump_mw` | 3.331,40 | 2.422 |
| `esios_capacity_installed` | `fuel_mw` | 7,95 | 2.422 |
| `esios_capacity_available` | `fuel_mw` | 7,90 | 2.422 |
| `esios_capacity_installed` | `autoconsume_battery_mw` | 5,00 | 1.206 |

Hay que separarlas en dos grupos, y la distinción es importante.

**Las cinco primeras son correctas.** España no ha conectado ninguna central nuclear ni
ningún ciclo combinado nuevo desde 2020, y la capacidad de bombeo tampoco se ha movido.
El dato refleja la realidad.

**Pero eso no las hace útiles.** Una columna con varianza cero no puede explicar nada en un
modelo. No es un problema de la base de datos: es un problema de selección de features.

**La sexta sí era sospechosa, y quedó confirmada.** `autoconsume_battery_mw` está clavada
en 5,00 MW, y no encajaba con lo que sabemos del sistema.

Comprobado contra su propia tabla, sin salir de la base de datos:

| Año | Días con dato | Valores distintos | Autoconsumo baterías | Autoconsumo solar FV |
|---|---|---|---|---|
| 2020 | 0 | 0 | — | 25,6 → 457,0 |
| 2021 | 0 | 0 | — | 481,7 → 752,2 |
| 2022 | 0 | 0 | — | 800,1 → 1.700,4 |
| 2023 | 245 | **1** | 5,00 | 1.991,2 → 3.719,3 |
| 2024 | 366 | **1** | 5,00 | 4.011,3 → 6.433,6 |
| 2025 | 365 | **1** | 5,00 | 6.696,7 → 8.472,2 |
| 2026 | 230 | **1** | 5,00 | 8.560,5 → 9.100,8 |

El hermano de la misma familia, el autoconsumo solar fotovoltaico, tiene **doce valores
distintos cada año** —uno por mes, como corresponde— y crece de 25 MW a 9.100 MW. La misma
tabla, el mismo pipeline y la misma carga mensual.

→ **El indicador 2366 está congelado en origen.** No es un fallo de ingesta: la serie no se
mueve en la fuente. `autoconsume_battery_mw` se descarta.

### Nota de método: dos intentos fallidos antes de acertar

El camino hasta esta comprobación merece anotarse, porque el error fue de diseño.

El primer intento consultó la API pidiendo un día suelto. Devolvió vacío para los cuatro
indicadores, incluido el de control. El segundo replicó la consulta del pipeline con
`time_trunc=month` y volvió a devolver vacío. Solo al imprimir la respuesta en crudo
apareció la causa: **HTTP 403 con una página de cortafuegos anti-bots**. Habíamos lanzado
más de cien peticiones en unos minutos desde una IP doméstica, y ESIOS la bloqueó.

Dos lecciones. La primera, que el `try/except` que devolvía `None` ante cualquier fallo
impedía distinguir «la API dice que no hay datos» de «la API no me deja entrar»; un test
que no puede distinguir eso no sirve. La segunda, que **la respuesta estaba en la base de
datos desde el principio**: si la carga histórica se hizo mes a mes, lo guardado *es* lo
que devolvía la fuente en cada momento. No hacía falta salir a Internet.

### La decisión

1. **Proponer el borrado de `trayport_daily`** tras revisar sus 22 menciones.
2. **Retirar el indicador 476** de `esios_capacity_available_fc.py`.
3. **No borrar las columnas constantes de la base**: el dato es correcto y tiene valor
   documental. Lo que hay que hacer es **excluirlas del conjunto de features**.
4. **Descartar `autoconsume_battery_mw` como feature**, por indicador congelado en origen.
   Se mantiene la columna y la ingesta: el dato es un registro fiel de lo que publica la
   fuente, y si ESIOS lo descongelara, el pipeline seguiría funcionando sin tocar nada.
   Mismo criterio que con las columnas constantes.

### La conclusión que se lleva a la reunión

`esios_capacity_installed` es **bastante menos informativa de lo que aparenta**. Tiene 24
columnas, pero cuatro son constantes y varias más apenas se mueven; las que de verdad
varían son solar, eólica e híbridos, que son justamente las tecnologías en crecimiento.

Conviene saberlo antes de que alguien añada las 24 al dataset esperando que aporten algo.
Y encaja con lo que ya sabíamos por otras vías: esta tabla es contexto estructural, no una
fuente de señal.

### Nota de método

Lo que iba a ser una limpieza de restos acabó siendo un hallazgo sobre la utilidad de una
tabla. Merece la pena anotarlo: el criterio de «vacía **y** sin menciones en el código»
descartó casi todos los candidatos de oídas, y en cambio la comprobación de varianza —que
no estaba en el plan— fue la que dio algo aprovechable.

### Implementación

- ✅ **Indicador 476 retirado** de `esios_capacity_available_fc.py` el 19-ago. El script
  pasa de ocho indicadores a siete. La línea se deja comentada en su sitio, con el motivo,
  por si ESIOS volviera a publicar la serie. Desplegado en el servidor.
- ✅ **Indicador 2366 comprobado** el 19-ago: congelado en origen, confirmado con la tabla
  año a año. `autoconsume_battery_mw` pasa a DESCARTAR en el Excel.
- ✅ **`autoconsume_solar_pv_mw` confirmada como buena** y marcada MANTENER: doce valores
  distintos por año, de 25 MW a 9.100 MW. Sirve además de validación cruzada de la D-03.

### Qué queda por hacer

1. Revisar las 22 menciones de `trayport_daily` antes de proponer el borrado.
2. Añadir un comentario junto al indicador 2366 en `esios_daily_capacity_instaled.py`,
   para que nadie vuelva a investigarlo desde cero dentro de tres meses.
3. Excluir las columnas constantes del conjunto de features cuando se construya el dataset
   (no borrarlas de la base: el dato es correcto).

---

*Siguiente decisión pendiente: las zonas horarias mezcladas entre tablas (bloqueante).*
