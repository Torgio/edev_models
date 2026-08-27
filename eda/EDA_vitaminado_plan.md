# EDA vitaminado · de 6 tablas a 22

**27-ago-2026** · Complemento de `EDA_avances_2026-08-27.md`

El EDA actual (`02_eda_bronze.ipynb`) es una **auditoría de calidad y de decisiones de fuente**:
resuelve qué columna usar cuando dos fuentes describen lo mismo, y qué huecos tiene la base.
Está bien hecho y cierra lo que se propuso cerrar. Pero corre sobre **6 de las 22 tablas** de
`tfm_energia`, y entre las 16 que faltan está `spot_price` — es decir, el EDA de un TFM de
predicción de precio **no ha mirado el precio todavía**.

Este documento es el plan para cerrar esa brecha, y `03_eda_vitaminado.ipynb` es su
implementación.

---

## 1 · Las 22 tablas, clasificadas

Tres criterios a la vez: **qué aporta**, **si tiene fuga**, y **si el EDA ya la mira**.

| Tabla | En el bronce | Frontera de fuga | Qué aporta al EDA | Prioridad |
|---|:---:|---|---|:---:|
| `spot_price` | ✗ | **TARGET** | Todo el bloque F: distribución, colas, estacionalidad, persistencia, spread | **1** |
| `forecast` | ✗ | SIN FUGA | Las features realmente usables; contraste previsión vs real | **1** |
| `esios_forecast_da` | parcial (3/12) | SIN FUGA | Faltan 9 de las 12 columnas que usa el maestro | **1** |
| `ecmwf_forecast_agg` | ✗ | SIN FUGA | Cuánto vale la "meteo perfecta" frente a ERA5 | **2** |
| `esios_capacity_available` | ✗ | CONDICIONAL | Cierra D-01, hoy imposible de verificar | **2** |
| `esios_capacity_installed` | ✗ | SIN FUGA | Ya contrastada vía A.4; validación cruzada de D-03 | 3 |
| `generation` | vía `esios_gen` | CON FUGA | 17 columnas; base de los lags D-1/D-7 | 3 |
| `entsoe_gen_data` | ✓ | CON FUGA | Ya analizada (A.1, E.1, E.2) | — |
| `esios_gen` | ✓ | CON FUGA | Ya analizada | — |
| `load_inter` | ✓ | CON FUGA | Ya analizada (A.2, C.1) | — |
| `entsoe_load_inter` | ✗ | CON FUGA | Posible duplicado de `load_inter`: **verificar cuál usa el maestro** | **2** |
| `era5_weather_agg` | ✓ | CON FUGA | Ya analizada (B.2) | — |
| `commodities` | ✓ | CON DESFASE (D−2) | Ya analizada; relación precio-gas en G.2 | — |
| `esios_pdbc_gen` | ✗ | CON FUGA | ¿Aporta con lag por encima de la generación real? | 3 |
| `esios_pbf_gen` | ✗ | CON FUGA | Programa base. Probablemente redundante con PDBC | 4 |
| `esios_pbf_bilateral` | ✗ | CON FUGA | Contratación bilateral | 4 |
| `esios_pbf_load_inter` | ✗ | CON FUGA | Programa de demanda e interconexiones | 4 |
| `entsoe_forecast_da` | ✗ | SIN FUGA | Segunda fuente de previsión: contraste con ESIOS | 3 |
| `trayport_daily` | ✗ | CON DESFASE | **Sin documentar.** Inspeccionar antes de decidir | 3 |
| `trayport_daily_ohlc` | ✗ | CON DESFASE | Idem. OHLC sugiere volatilidad explotable | 3 |
| `trayport_trades` | ✗ | CON DESFASE | Idem. Verificar granularidad y unidad | 4 |
| `pipeline_log` | ✗ | OPERATIVA | **No es feature.** Sirve para explicar los huecos de ingesta | **2** |

**Lectura rápida.** Con las cuatro de prioridad 1 el EDA pasa de auditoría a EDA de verdad. Las
de prioridad 4 probablemente no lleguen antes de septiembre, y está bien: es mejor declararlas
como fuera de alcance que dejarlas a medias.

### Dos observaciones que salen de mirar la lista completa

**`entsoe_load_inter` y `load_inter` coexisten.** El EDA de bronce usa `load_inter` con columnas
`load_inter_entsoe_load` y `load_inter_ree_load`, así que parece la tabla consolidada. Pero si
`entsoe_load_inter` es una tabla separada con la misma información, hay dos caminos de ingesta
para el mismo dato y conviene saber cuál llega al maestro — sobre todo ahora, que la pregunta
"¿qué columna de demanda usa el maestro?" sigue abierta desde el hallazgo C.1.

**`pipeline_log` es la respuesta al hallazgo C.1, no una tabla más.** Los 9 ceros espurios de
`entsoe_load` del 30-jun/1-jul-2026 son, con toda probabilidad, un fallo de ingesta que quedó
registrado ahí. Cruzar las horas problemáticas contra el log convierte una hipótesis
("pinta a corte de ingesta") en **evidencia documentada**, que es exactamente el estándar que
este proyecto se puso. Es media hora de trabajo y cierra un hallazgo.

---

## 2 · Qué añade el notebook vitaminado

`03_eda_vitaminado.ipynb`, seis bloques nuevos. Todos con el mismo formato
contexto → decisión → evidencia → consecuencia, y todos con la etiqueta de fuga pegada.

### Bloque F · El target

Ocho apartados: cobertura y huecos, distribución y colas, curva pato del precio por año,
estacionalidad mes×hora, regímenes marcados (apagón y excepción ibérica), persistencia
(autocorrelación hasta 8 días), diferencial diario máx−mín, y contraste entre las fuentes del
target si hay más de una.

**El que más rinde es F.6 (persistencia).** Fija con un número la vara contra la que se mide
todo lo demás: si el modelo con features exógenas no supera claramente a la persistencia D−1, no
está aportando nada. Es la justificación teórica de `F11_baselines.ipynb`, que hoy existe sin
ella.

### Bloque G · Qué mueve el precio, con la etiqueta de fuga pegada

Correlaciones precio-driver, pero con una columna `disponible_12h` **obligatoria** en cada tabla.
Se calcula además el ranking restringido a features sin fuga: la diferencia entre el mejor |r|
con fuga y el mejor sin fuga **mide directamente cuánto cuesta la restricción de producción**.
Eso es un párrafo que la memoria necesita y que hoy no se puede escribir.

### Bloque H · La calidad de lo que sí podemos usar

Probablemente **el bloque de mayor valor añadido de todo el EDA**, y el que no existe en ninguna
parte del proyecto. Las features sin fuga son previsiones, y una previsión trae su propio error:
si la previsión de eólica de REE se equivoca 1.500 MW de media, el modelo hereda ese error
entero. Ningún algoritmo recupera información que la entrada no tiene.

Tres cosas: (H.1) error de las previsiones de REE contra la realidad, desglosado por hora y por
mes; (H.2) ECMWF contra ERA5, que da la ablación honesta de "meteorología perfecta"; (H.3) un
test indirecto de D-01 mientras no exista `esios_capacity_available_fc`.

Si el MAE de la previsión solar se dispara en las horas centrales y en primavera, es exactamente
donde el precio es más difícil — y explica **de antemano** por qué el modelo va a fallar ahí.
Ese emparejamiento (dónde falla la entrada ↔ dónde falla el modelo) es un resultado de memoria,
no un detalle técnico.

### Bloque I · PDBC/PBF

Mide si el programa con lag aporta algo por encima de la generación real con el mismo lag. Si no
aporta, se descarta y se ahorra una familia entera de tablas. Un descarte documentado vale tanto
como una confirmación.

### Bloque J · Contexto europeo y commodities

Reaprovecha la tarea de MongoDB (dataset de Ember: 33 países, precios horarios, 2015-2025) con
cuatro figuras — media anual por país, curva pato española por año, desacople ES-PT, y la
validación cruzada de Ember contra el `spot_price` propio, que es una comprobación externa de la
ingesta del target prácticamente gratis. Añade la inspección de las tablas `trayport_*`.

### Bloque K · La tabla que se lleva el feature engineering

El entregable operativo: columna, veredicto de fuga, tratamiento. Lo que no esté ahí no entra al
maestro; lo que esté, entra con el tratamiento indicado. Es lo que convierte el EDA en algo que
la fase siguiente puede usar sin releer 2.500 líneas de notebook.

---

## 3 · Decisiones de diseño del notebook (y por qué)

**Resolución de esquema en vez de nombres a mano.** Las tablas nuevas no están en
`bronze_config.py` y no conozco sus nombres reales de columna. En vez de escribirlos de memoria
—el error que este proyecto ya pagó caro— cada bloque resuelve su columna con `pick()` sobre una
lista de candidatos y **se salta solo** si no la encuentra, registrando el motivo en `OMITIDOS`.
La última celda imprime todo lo que no se pudo correr: un bloque saltado en silencio es peor que
uno que falla.

**`validate="one_to_one"` en todos los merges.** El incidente de la duplicación ×24 pasó
desapercibido durante un EDA entero porque nada lo comprobaba. Con este parámetro, pandas lanza
la excepción en el momento del join en lugar de propagar filas multiplicadas hasta el final.

**Hora local, siempre, en los perfiles horarios.** Un perfil horario en UTC mezcla dos horas
distintas del día español según sea invierno o verano, y aplana justo la caída solar del
mediodía, que es el rasgo que la figura quiere mostrar.

**Regímenes marcados como constantes, no filtrados a mano.** `APAGON` y `EXCEPCION_IBERICA` son
constantes al principio del notebook, y cada bloque decide si excluye o colorea. Las fechas de
la excepción ibérica **siguen pendientes de verificar contra el BOE**: están puestas de memoria
y condicionan F.5 y G.2.

---

## 4 · Orden de trabajo

| # | Paso | Desbloquea | Coste |
|---|---|---|---|
| 1 | `python scripts/inspect_schema.py` | Todos los nombres de columna + genera `docs/columnas_bronce_eda.md` | 10 min |
| 2 | Resolver la duplicación ×24 y regenerar el unificado | Todos los conteos absolutos | 30 min |
| 3 | Añadir `spot_price` al bronce | **Bloque F entero** | 30 min |
| 4 | Añadir `forecast` completo (12 columnas) | Bloques G.1 y H.1 | 30 min |
| 5 | Correr `03_eda_vitaminado.ipynb` de arriba a abajo | La lista de bloques omitidos, que es la lista de trabajo real | 20 min |
| 6 | Cruzar C.1 contra `pipeline_log` | Cierra el hallazgo de los ceros espurios | 30 min |
| 7 | Añadir `ecmwf_forecast_agg` | Bloque H.2 (ablación de meteo perfecta) | 45 min |
| 8 | Extraer `esios_capacity_available_fc` con `(run_date, target_date)` | Cierra D-01 | 2 h |

Los pasos 1 a 5 son medio día y transforman el alcance del EDA. El 8 es el único caro, y si no
entra en el calendario se declara como limitación conocida en la memoria — que es una respuesta
perfectamente válida, siempre que sea explícita.

---

## 5 · Ficheros de esta entrega

| Fichero | Dónde va | Qué hace |
|---|---|---|
| `03_eda_vitaminado.ipynb` | `notebooks/` | Los seis bloques nuevos. Corre aunque falten tablas |
| `inspect_schema.py` | `scripts/` | Radiografía de las 22 tablas → genera `docs/columnas_bronce_eda.md` |
| `bronze_config_v2_patch.py` | `scripts/` (para revisar y pegar) | Entradas propuestas + los dos guardarraíles de integridad |

Ninguno pisa nada existente: el notebook es nuevo, el script de esquema es de sólo lectura, y el
patch de configuración está pensado para revisarse y pegarse a mano, no para importarse tal cual.
