# EDA · revisión completa del estado

**28-ago-2026** · Sustituye a `EDA_avances_2026-08-27.md`, que se escribió sobre un parquet
corrupto y con los nombres de columna sin verificar.

**Base de evidencia:** `bronze_unificado.parquet` regenerado el 28-ago (58.319 filas × 83
columnas, 2019-12-31 23:00 → 2026-08-26 21:00 UTC), radiografía de las 22 tablas de
`tfm_energia`, y corridas completas de `02_eda_bronze_v2` y `03_eda_vitaminado_v2`.

---

## 0 · Lo que ha cambiado respecto de la versión anterior

Cuatro afirmaciones del documento del 27-ago eran incorrectas. Conviene dejarlas escritas
porque dos de ellas afectan a decisiones ya tomadas.

| Afirmación del 27-ago | Lo que dicen los datos | Consecuencia |
|---|---|---|
| «E.2 no confirma lo documentado: correlación −0,004, sin negativos» | **Confirma**: corr 0,866, mínimo −3.710 MW, 8.831 filas negativas al mediodía | El resultado anterior era artefacto del parquet duplicado. La decisión sobre hidráulica se sostiene |
| «El parquet tiene 24 filas por hora, causa desconocida» | `esios_capacity_available` declarada `daily` siendo horaria → merge muchos-a-muchos | Resuelto. Guardarraíl añadido |
| «Los 9 ceros de `entsoe_load` parecen un corte de ingesta» | `pipeline_log` lo documenta: 72 reintentos fallidos los días 29 y 30-jun | De hipótesis a evidencia con hora |
| «`wind_gust10_mean` tiene un hueco sin causa» | Es junio-2026 completo, bloque continuo de 240 pasos | Fallo de una ejecución. Reparable |

**Lección de método, que vale más que cualquiera de las cuatro:** el EDA de agosto corrió sobre
un parquet con 1.393.183 filas donde debía haber 58.056 y **nadie lo detectó**, porque los
porcentajes y las correlaciones sobreviven a una duplicación uniforme. Sólo fallaban los conteos
absolutos, y no se contrastaban contra nada. Ahora hay un `assert` que lo caza en el momento del
merge.

---

## 1 · Decisiones confirmadas con evidencia propia

Las tres se pueden llevar a `docs/decisiones_datos.md` como validación independiente. No
reemplazan a las decisiones del equipo: las respaldan con una segunda corrida.

### D-02 · El B04 de ENTSO-E no es el CCGT de ESIOS — **confirmado**

| Contraste | Correlación | Diferencia media |
|---|---|---|
| `entsoe_gas_mw` vs CCGT solo | 0,97876 | +1.890,7 MW |
| `entsoe_gas_mw` vs CCGT + cogeneración | **0,99627** | −374,2 MW |

Y el offset contra la suma **no es estable**: −806 MW (2020) → −558 → −341 → −257 → −222 → −177
→ −192 (2026). Cae a una cuarta parte en seis años.

**Consecuencia.** El B04 es la suma, no el ciclo combinado puro. Usar una fuente en train y otra
en test introduciría un sesgo que varía con el tiempo — la peor clase de sesgo, porque imita una
señal real.

### D-03 · El autoconsumo entra por los dos lados del balance — **confirmado**

El mes de quiebre no se fijó a mano: se detectó comparando contra la desviación del período
previo (umbral 49 MW). Salió **2025-12** por los dos caminos.

| Mes | `ree_load − entsoe_load` | `ree_gsolar − calc_solar_fv` |
|---|---|---|
| 2025-11 | 1,3 MW | 6,7 MW |
| **2025-12** | **434,7 MW** | **435,7 MW** |
| 2026-07 | 2.495,4 MW | 2.194,3 MW |

**Correlación entre las dos series mensuales: 0,9984.** El perfil horario del autoconsumo
estimado es una campana con pico a las 13 h (4.054 MW) y mínimo a las 6 h (−9 MW).

**Consecuencia.** Es el mismo fenómeno entrando por demanda y por generación, no dos problemas
distintos. No usar `ree_load` ni `ree_gsolar_mw` directos; la FV limpia es `calc_solar_fv_mw`.

*Validación cruzada por una vía independiente:* `cap_inst_autoconsume_solar_pv_mw` crece de 26 a
9.125 MW entre 2020 y 2026. Una tabla de potencia instalada, que no interviene en la resta de
series horarias, cuenta la misma historia.

### D-04 · Columnas constantes — **confirmado**

Seis columnas con varianza exactamente cero en seis años y medio: `cap_inst_pump_mw` (3.331,40),
`cap_inst_nuclear_mw` (7.117,29), `cap_inst_ccgt_mw` (24.561,85), `cap_inst_fuel_mw` (7,95),
`cap_disp_fuel_mw` (7,90) y `cap_inst_autoconsume_battery_mw` (5,00 — el indicador 2366
congelado en origen).

**Consecuencia.** El dato es correcto: España no conectó nucleares ni ciclos combinados nuevos
desde 2020. Pero una varianza cero no puede explicar nada en un modelo. Se traen como evidencia,
se excluyen del conjunto de features.

---

## 2 · Bloques cerrados en negativo

Un descarte documentado con evidencia vale tanto como una confirmación, y es lo que separa un
EDA de una galería de gráficos.

### A.5 · `demanda_residual_prev_mw` **no** arrastra autoconsumo

La diferencia contra `demanda_mercado_prev_mw` deriva suavemente de −13.143 MW (jun-2025) a
−15.043 MW (ago-2026), **sin salto en dic-2025**. Es la resta de renovables que "residual"
implica conceptualmente, creciendo con la penetración renovable.

**Consecuencia.** La exclusión de esta columna del maestro se sostiene por su motivo original
—se revisa 10-14 días después de publicada— y sólo por ese. No hay un segundo motivo.

### B.2 · La censura por viento bajo queda descartada

`era5_wind_gust10_mean` tiene 0,41 pp de nulos por encima del patrón estructural de 3 h. La
hipótesis de que "ráfaga cercana a cero" se guardara como nulo **se prueba y se descarta**: el
mínimo real de la columna es 2,774 m/s, no hay ceros exactos, y el viento medio en las horas sin
ráfaga (3,558 m/s) es similar al de las horas con ella (3,864 m/s). La causa real está en §4.

### F.2 · El cambio de entorno de ERA5 **no** fue metodológico

El campo `tensor_path` cambia de `/home/ubuntu/scripts/...` a `\data\era5_tensors\` en
enero-2025, y así sigue durante 20 meses. La sospecha era que un cambio de malla o de agregador
introdujera un sesgo invisible en el perfil de nulos, justo en el período de test.

Comparando mes contra mes del año a ambos lados del corte, **todas las variables quedan por
debajo del 1 %**, y `msl_mean` —la más estable, y por tanto el mejor testigo— en −0,10 %.
`tp_mean` marca 16,6 %, pero es precipitación: varía así entre años por naturaleza.

**Consecuencia.** El cambio fue de infraestructura, no de método. Se documenta y se cierra.
Merecía comprobarse: era el tipo de discontinuidad que aparece en la defensa si no la detectas
tú.

---

## 3 · Acciones concretas sobre el dataset de modelado

### B.1 · Bombeo: convención asimétrica — **confirmado, con un matiz nuevo**

| | nulos | ceros exactos |
|---|---|---|
| `entsoe_pumping_gen_mw` | 43,85 % | 5,58 % |
| `entsoe_pumping_cons_mw` | 0,06 % | 23,18 % |

25.535 filas tienen `gen` nulo con `cons` con dato: el **99,9 %** de todos los nulos de `gen`.

**El matiz que la versión anterior no veía:** `gen` tiene además un 5,58 % de ceros explícitos.
La convención no es "siempre NULL", es "casi siempre NULL, a veces 0". No cambia la corrección,
pero conviene decirlo con precisión en la memoria en vez de simplificar.

**Corrección, en `_features_lag_reales` del maestro:**

```python
h["pumping_gen_mw"] = h["pumping_gen_mw"].fillna(0)   # NaN aquí = "no hubo turbinación"
```

Sin esto, `.agg(["mean","min","max"])` ignora los NaN en vez de contarlos como cero: la media
diaria se calcula sólo sobre las horas con turbinación real, no sobre el día completo. Infla la
media y esconde el mínimo. **Sólo sobre esa columna** — en cualquier otra, un NaN sí significa
"no se sabe".

### C.1 · Los 9 ceros de `entsoe_load` — **confirmado, con causa documentada**

Nueve filas de 58.319 (0,0154 %):

- `2026-03-17 11:00 UTC` — aislada
- `2026-06-30 23:00` → `2026-07-01 06:00` — ocho consecutivas

**La causa ya no es una hipótesis.** `pipeline_log` registra que `entsoe_daily` del 29 y del
30-jun-2026 quedó en estado `parcial` y **reintentó 72 veces cada día**, siempre con 0 insert y
0 update. El primer intento sí insertó 24 filas y se marcó parcial: metió los registros con
ceros, y ningún reintento los corrigió. El cero aislado del 17-mar no aparece en el log, lo cual
también es información.

**Impacto medido:** `calc_autoconsumo_mw` llega a **33.597 MW** contra un rango confirmado de
~2.500 MW en A.2. El error se propaga entero a la derivada.

**Corrección propuesta:** `entsoe_load_inter.actual_load_mw` es un segundo camino para el mismo
dato. Si tiene valor bueno en esas 9 horas, **reparar es mejor que imputar**.

### H.1 · Las previsiones de REE tienen sesgo, y es corregible

Éste es el resultado más aprovechable de todo el EDA vitaminado, y no existía antes.

| Variable | MAE | sesgo | MAPE | corr |
|---|---|---|---|---|
| demanda | 257 MW | +0,8 MW | 0,95 % | 0,99 |
| eólica | 772 MW | **+128,5 MW** | 15,8 % | 0,95 |
| solar FV | 547 MW | **+197,8 MW** | 65,2 % | 0,98 |

La previsión de demanda es excelente y prácticamente insesgada. Pero **eólica y solar están
sistemáticamente sobreestimadas**. Un sesgo constante se corrige antes de que entre al modelo;
el ruido no. Ignorarlo es regalar precisión.

*El MAPE del 65 % en solar no es lo que parece:* divide entre valores diminutos al amanecer y al
anochecer. El MAE de 547 MW es la cifra honesta.

**Consecuencia para el modelado.** Las features sin fuga son previsiones, y una previsión trae
su propio error. Si la entrada se equivoca 772 MW en eólica, el modelo hereda ese error entero:
ningún algoritmo recupera información que la entrada no tiene. Esto fija un suelo al error
alcanzable y merece un párrafo en la memoria.

---

## 4 · Hallazgos abiertos

### C.2 · La solar negativa nocturna no encaja del todo

| Columna | filas negativas | mínimo | % nocturnas |
|---|---|---|---|
| `esios_gen_ree_gsolter_mw` (termosolar) | 6 | −126,58 MW | **17 %** |
| `esios_gen_ree_gsolar_mw` (FV) | 67 | −62,83 MW | **100 %** |

La explicación documentada —consumo auxiliar de plantas termosolares por la noche, seguidores y
bombeo de sales— predice lo contrario: los negativos deberían concentrarse en la termosolar y
ser nocturnos. Aquí la FV cumple al 100 % y la termosolar sólo al 17 %.

Seis filas es residual y no bloquea nada, pero **conviene mirar esas seis antes de escribir la
explicación en la memoria**. Si son diurnas, la explicación no vale para ellas.

### E.2 · Hidráulica — confirmado, tras corregir el artefacto

Correlación `ree_ghidro` contra la suma limpia de ENTSO-E: **0,866**. Mínimo **−3.710 MW**.
**8.831 filas negativas**, concentradas al mediodía — el pico de bombeo, cuando sobra renovable.

Confirma que `ree_ghidro_mw` mezcla generación y consumo de bombeo en la misma columna, y que
`entsoe_hydro_run_river_mw + entsoe_hydro_reservoir_mw` es la fuente limpia.

La corrida de agosto daba −0,004 y cero negativos. Ese resultado era el artefacto.

### F.1 · ERA5: junio-2026 sin ráfaga

240 pasos de 3 h, el mes completo del día 1 a las 00:00 al 30 a las 21:00, bloque continuo, con
el resto de variables intactas. Un mes entero y una sola variable: fallo de una ejecución, no un
patrón. **Reparable reingiriendo el mes.**

### D-01 · Sigue sin poder verificarse, y ahora se sabe por qué

El diagnóstico contra la base dice que la capacidad **sí varía dentro del día**: 1.882 de 2.432
días tienen más de un valor de `ccgt_mw`. La tabla es horaria de verdad.

Pero el parquet actual está deduplicado a mano (una fila por fecha) como solución provisional
para desbloquear el merge, así que el bloque H.3 mide el parquet, no la fuente. Sus resultados
—cero variación intradía— son un artefacto de esa deduplicación.

**Lo que sí es informativo:** `cap_disp_ccgt_mw` correlaciona 0,12 con la generación real del
mismo día y 0,13 con la del anterior. Prácticamente idénticas y ambas bajísimas. Como feature
aporta poco de todas formas, con fuga o sin ella.

Cerrar D-01 de verdad sigue necesitando `esios_capacity_available_fc` con clave
`(run_date, target_date)`.

---

## 5 · Deuda técnica pendiente

### 5.1 · El grain de `esios_capacity_available` (bloqueante silencioso)

`scripts/bronze_config.py`, línea 72: dice `"grain": "daily"` y la tabla es horaria. Hoy está
parcheado deduplicando el parquet a mano, lo que significa que:

- **la próxima extracción vuelve a romper el merge**;
- se descarta la variación intradía real de 1.882 días;
- H.3 y la figura L.11 del notebook 04 miden un artefacto.

Es un cambio de una palabra que sigue sin hacerse.

### 5.2 · `spot_price` fuera del bronce

Seis bloques del notebook 03 se omiten por esto: F (el target entero), G (drivers), I (PDBC) y
J (contexto europeo). La tabla tiene **14 columnas de precio**, con España por tres fuentes
—medias 85,84 / 85,84 / 85,85, casi seguro intercambiables— y **once zonas europeas más**.

Eso además hace innecesario el dataset externo de Ember: el análisis de congestión ES-PT y ES-FR
sale de datos propios, cubriendo exactamente el mismo período que el target.

**Comprobación pendiente antes de analizar colas:** el rango español es exactamente [−15, 700],
con dos topes redondos, mientras Francia va de −496,86 a 2.987,78. Si hay muchas horas pegadas a
los topes, el target está censurado y las colas que se midan no son las verdaderas.

### 5.3 · `esios_forecast_da`: 4 columnas de 14

El bronce trae cuatro. Diez de las restantes **duplican `forecast` hasta el decimal**
(verificado: `ree_demanda_prev` y `demanda_mercado_prev_mw` coinciden en mínimo, máximo y
media). Las cuatro que sí aportan son `demanda_prev_mw`, `demanda_residual_prev_mw`,
`gen_solartermica_prev_mw` y `cap_baleares_prev_mw`.

### 5.4 · `entsoe_forecast_da` se reescribe hacia atrás

Las medias históricas **cambiaron entre dos ejecuciones del mismo día** (solar: 4.487,24 →
4.489,37; eólica: 6.629,84 → 6.629,65). La tabla se está actualizando retroactivamente: el valor
almacenado no es el que se publicó en su momento.

Es el mismo problema que llevó a excluir `demanda_residual_prev_mw` del maestro, y el mismo que
hay detrás de D-01. **Una previsión revisada es fuga con otro disfraz.** Antes de usar esta
tabla hay que medir cuánto se revisa.

### 5.5 · Trayport, sin documentar y mejor que `commodities`

`trayport_daily_ohlc` cubre TTF y EUA desde 2020-01-02 —la misma ventana que `commodities`— y
añade curva forward por `periodo`, apertura/máximo/mínimo/cierre, vwap, volumen, número de
operaciones y porcentaje de agresor comprador. De ahí salen tres features que `commodities` no
puede dar: pendiente de la curva (expectativa del mercado), rango intradía (volatilidad
realizada) y presión compradora.

Está en formato largo (~7 filas por fecha): sin pivotar, multiplicaría el calendario ×7.
**Pendiente:** confirmar a qué hora queda fijado el cierre. Si es hacia las 17:30 como TTF y
EUA, el último dato disponible a las 12:00 de D es el de D−2.

### 5.6 · ECMWF: 168 filas, pero puede haber histórico fuera

La tabla es una ventana móvil de inferencia, no un histórico, así que la ablación de
"meteorología perfecta" no se puede hacer como estaba planteada. **Consecuencia para la memoria:
todo resultado que use meteorología es una cota superior optimista**, porque se apoya en ERA5,
que es reanálisis.

Pero `tensor_path` y `tensor_index` apuntan a ficheros `.npy` por `run_date` en el VPS. Un `ls`
sobre `/home/ubuntu/scripts/ingesta/tensors/ecmwf_forecast/` resuelve si el histórico existe
fuera de la base. Si existe, la ablación vuelve a ser posible.

---

## 6 · Verificaciones de integridad que sí pasan

Conviene decirlas, porque un EDA que sólo enumera problemas da una impresión falsa de la base.

- **Cobertura temporal íntegra.** Cero horas ausentes del calendario UTC en 58.319 horas.
- **DST correcto.** 13 días con ≠24 horas, y son exactamente los cambios de horario: siete de
  23 h y seis de 25 h, uno por año. `date_local`/`hour_local` reflejan el cambio real sin
  duplicar ni perder filas. Se puede confiar en cualquier agregación diaria del EDA.
- **Eólica intercambiable entre fuentes.** Correlación 0,99960 entre `entsoe_wind_mw` y
  `esios_gen_ree_gwind_mw`, diferencia porcentual **mediana** de 0,005 %. (La máxima absoluta es
  317 % por dividir entre denominadores diminutos en horas de viento casi nulo: la mediana es la
  métrica honesta aquí, no el máximo.)
- **Perfil de nulos sin sorpresas.** El 95,83 % de las tablas diarias es 23/24 por diseño, y el
  ~66,9 % de ERA5 es su paso trihorario. No son huecos.

---

## 7 · Orden de trabajo

| # | Acción | Desbloquea | Coste |
|---|---|---|---|
| 1 | `"grain": "hourly"` en la línea 72 de `bronze_config.py` | Que el merge no se rompa en cada extracción; H.3 y L.11 reales | 1 min |
| 2 | Añadir `spot_price` a `bronze_config.py` y reextraer | **Bloques F, G, I y J del notebook 03** | 30 min |
| 3 | Comprobar la censura del target en −15 y 700 | Que el análisis de colas sea válido | 5 min |
| 4 | Mirar las 6 filas diurnas de termosolar negativa (C.2) | Cerrar el hallazgo o reescribir su explicación | 15 min |
| 5 | `fillna(0)` de `pumping_gen_mw` en el maestro | Features de bombeo correctas | 5 min |
| 6 | Reparar los 9 ceros con `entsoe_load_inter` | `calc_autoconsumo_mw` utilizable | 30 min |
| 7 | Medir cuánto se revisa `entsoe_forecast_da` | Saber si es usable | 30 min |
| 8 | `ls` de los tensores ECMWF en el VPS | Si hay histórico, recupera la ablación de meteo | 2 min |
| 9 | Reingerir junio-2026 de ERA5 | Cierra F.1 | 20 min |
| 10 | Extraer `esios_capacity_available_fc` | Cierra D-01 | 2 h |

Los pasos 1 a 3 son cuarenta minutos y transforman el alcance: el EDA pasa de auditar la base a
analizar el problema de predicción.

---

## 8 · Qué va a la memoria

Seis figuras, cada una atada a una decisión que se toma en otro sitio del TFM:

| Figura | Respalda | Origen |
|---|---|---|
| Serie mensual del autoconsumo con el quiebre de dic-2025 marcado | D-03 | 02 · A.2 |
| Offset anual `entsoe_gas` − suma ESIOS | D-02 | 02 · A.1 |
| MAE de las previsiones de REE por hora y por mes | El suelo de error del modelo | 03 · H.1 |
| Cobertura de la demanda por eólica y FV | El cambio estructural del precio | 04 · L.4 |
| Demanda residual por hora y año | La forma horaria que el modelo aprende | 04 · L.6 |
| Precio frente a commodities por régimen | Tratar la excepción ibérica aparte | 05 · M.10 |

Y tres párrafos de limitaciones declaradas, que valen tanto como las figuras: la meteorología
como cota superior (§5.6), las previsiones revisadas retroactivamente (§5.4), y D-01 sin cerrar
(§4).

---

## Anexo · Trazabilidad

Todos los números provienen de las corridas del 28-ago-2026 de `02_eda_bronze_v2.ipynb` y
`03_eda_vitaminado_v2.ipynb` sobre `bronze_unificado.parquet` (58.319 filas), más la radiografía
de la base de `eda/columnas_clave.py` y `eda/inspect_schema.py`.

Los resultados del bloque H.3 están afectados por la deduplicación provisional del parquet de
capacidad descrita en §5.1 y deben re-verificarse tras corregir el grain.

Las decisiones D-01 a D-04 se citan por su código y viven en `docs/decisiones_datos.md`. Una
decisión cerrada no se rediscute: si cambia, se abre una nueva con fecha.
