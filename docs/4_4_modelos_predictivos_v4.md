> **Nota de trabajo (no forma parte del informe).** v4: modelos agrupados por familia; solo se desarrollan los de mas peso en el modelo final. Cifras de `data/gold/finales_v2_nucleo` (ancla D).

# 4.4 Modelos predictivos

Todos los modelos entregan las 24 horas del precio de D+1 con la información de las 11:00 de D, de modo distinto según el grupo: los estadísticos, como 24 pasos de la serie horaria; los lineales y el LightGBM global, con un único modelo sobre filas horarias; el *boosting* horario, con uno por hora; las redes *encoder-decoder*, con un *decoder* que recorre las horas, y las de salida densa, con un vector de 24 valores. El *boosting* horario y el denso usan una vista plana por día: precio horario de D, estadísticos de cada canal en 168 horas, bloque de D+1 y constantes. Salvo el seq2seq absoluto, las redes y el *boosting* horario predicen el residuo tipificado frente al precio de D (ancla D), para no aprender el nivel.

La partición es temporal: entrenamiento de 2020 a 2024, validación en 2025 y *test* de enero a julio de 2026, que no fija composición ni pesos; escaladores y filtros se ajustan solo con entrenamiento. Las redes y el *boosting* horario usan pérdida de Huber y parada temprana; estos y el LightGBM global, tres semillas, y los deterministas, una. La referencia, que toda técnica debe batir, es la persistencia (*naive*): cada hora de D+1 toma el precio de esa hora en D.

## Aprendizaje automático clásico

### Modelos estadísticos y lineales

Son la base que todo modelo complejo debe superar. **SARIMA**(3,1,0)(1,0,0), de periodo 24 horas, es el único que necesita avanzar día a día recibiendo, tras predecir cada jornada, sus valores observados; solo ve el precio. **SARIMAX**, pese al nombre, quedó en (0,0,0)(0,0,0): una regresión sobre exógenas tipificadas con constante, sin la cual no representa el nivel, cuya predicción no alteran los días observados; frente a Ridge, no penaliza, y su estimación por máxima verosimilitud es lenta y sin tipificar no converge. **Ridge** es un único modelo lineal con penalización L2 para todas las horas, y **ElasticNet** acabó en L1 pura, un Lasso. Los tres últimos filtran por correlación de Spearman con el precio (significativa y |ρ| ≥ 0,10; de cada par a 0,85 o más, la más correlada), protegiendo hora y calendario. Ninguno capta la respuesta escalonada del precio a la curva de oferta (SARIMA no ve exógenas y los demás las combinan linealmente, con un coeficiente por variable para todas las horas) ni entra en el modelo final.

### Árboles de *gradient boosting*

El ***boosting* horario** ajusta 24 LightGBM sobre la vista plana, uno por hora, que ven el día entero y paran cada uno por su cuenta, sin compartir nada. El **LightGBM global** es un único modelo sobre filas horarias, en las que cada hora solo ve sus variables. Un **LightGBM** y un **XGBoost** globales de configuración estándar, sin búsqueda, no fueron candidatos por entrenarse fuera del arnés común, con retardos vetados del ERA5, corte por fecha de emisión y sin la hora que falta en el cambio horario de marzo. El *boosting* horario sí lo fue, sin ser elegido.

**LightGBM global.** *Por qué.* Tratar las 24 horas como muestras de un mismo fenómeno multiplica los ejemplos sin resumir la matriz. *Cómo.* Ajusta el precio sin tipificar con pérdida cuadrática y la hora como seno y coseno; Optuna, con 300 pruebas, fijó 1.900 árboles, 27 hojas, profundidad 10 y submuestreo de filas y columnas, sin parada temprana. *Bondades.* Lo que no explica no queda en la forma de D, y se sirve con las 24 filas de D+1, sin ventana ni escaladores. *Desventajas.* No impone coherencia al día, y sus hiperparámetros se afinaron con una versión anterior de la matriz. *Qué aporta al conjunto.* Tiene el mayor peso, y sus errores están, con los del seq2seq absoluto, entre los menos correlados con el resto.

## Aprendizaje profundo

### Redes *encoder-decoder*

Comparten arquitectura: un *encoder* bidireccional resume las 168 horas observadas; su estado y las constantes proyectadas acompañan a las variables conocidas de cada hora de D+1, y un *decoder* recurrente recorre las 24 horas con salida común. Cada variante cambia una pieza respecto al **seq2seq**, con celdas LSTM de 96 unidades y objetivo residual: la **GRU**, la celda; la **SimpleRNN**, una celda sin puertas de 64 unidades; la **Conv1D-LSTM**, una convolución causal previa que reduce la ventana a 42 pasos, y el **seq2seq absoluto** cambia el objetivo. La SimpleRNN y la Conv1D-LSTM quedan fuera; la GRU y el seq2seq entran con peso pequeño: cometen casi los mismos errores y, elegido uno, el otro apenas aporta.

**Seq2seq absoluto.** *Por qué.* Aísla lo que aporta el objetivo residual. *Cómo.* Conserva la arquitectura del seq2seq, con *decoder* LSTM unidireccional de 96 unidades y capa lineal común, pero predice el precio tipificado, sin sumarle el de D. *Bondades.* Lo que no explica no reproduce la forma de D, y es la red que mejor sitúa la hora pico. *Desventajas.* Debe aprender también el nivel, y lo que no explica tiende a la media de 2020-2024, elevada por la crisis de 2022; en solitario es la red de mayor error. *Qué aporta al conjunto.* Diversidad: sus errores son los menos parecidos a los del LightGBM global.

### Redes de salida densa

Llevan el bloque de D+1 aplanado, mediante capas densas, a una salida con pesos propios por hora, sin *decoder*: cada hora ve las previsiones de todo D+1, pero debe localizar sus columnas en ese bloque. La **LSTM directa** conserva el *encoder* sobre la secuencia y mide así cuánto aporta el *decoder*; el **denso** prescinde del orden temporal y usa la vista plana. La LSTM directa entra con peso pequeño: sus errores se parecen a los del denso, probablemente porque ambos procesan ese bloque en capas densas.

**Denso.** *Por qué.* Si una red sin orden temporal igualara a las recurrentes, la secuencia no aportaría. *Cómo.* Capas ocultas de 512 y 256 unidades con ReLU y *dropout* de 0,2, y salida lineal de 24, sobre unas 800 entradas. *Bondades.* Al no recorrer secuencias, su cómputo es muy inferior al de las recurrentes, aunque es el modelo con más parámetros, unos 547.000. *Desventajas.* Salvo el precio de D, los resúmenes de la vista plana borran cuándo ocurrió cada valor. *Qué aporta al conjunto.* Es el segundo en peso, casi a la par del LightGBM global.

## Modelo final: el *ensemble* seleccionado

*Por qué.* Promediar compensa errores tanto más cuanto menos correlados estén, y los candidatos difieren en entrada y objetivo. *Cómo.* Selección voraz de Caruana et al. (2004), solo con validación, sobre la mejor semilla de cada familia: durante 30 pasos se añade, con reemplazo, el candidato que deja menor error absoluto medio del promedio; se conserva el tamaño de menor error, y el peso de cada modelo, igual en las 24 horas, es la fracción de veces elegido. Quedan el LightGBM global (11 de 30), el denso (10), la LSTM directa y el seq2seq absoluto (3 cada uno), la GRU (2) y el seq2seq (1).

Sustituye a la regla anterior, que promediaba a partes iguales a toda familia que batiera a la persistencia en validación, con lo que entraban Ridge y SARIMAX, peores que ella en *test*; como la revisión se motivó al observar el *test*, composición, pesos y aportaciones se deciden y miden solo en validación. *Bondades.* Deja fuera, sin umbral previo, a las familias que nunca son la mejor adición, y sus pesos, múltiplos de un treintavo, dan menos margen para sobreajustar la validación que unos continuos. *Desventajas.* Exige servir seis modelos con tres entradas y dos objetivos, y reentrenar uno obliga a repetir la selección; además, como el error apenas varía tras diez pasos y su mínimo cae en el tope de 30, conserva adiciones casi inútiles, como la del seq2seq.

### Evidencia de la selección

La elección se hizo con validación y el *test* solo la confirma. El MAE de validación del *ensemble* seleccionado es optimista, pues ese año ya sirvió para detener el entrenamiento, elegir semillas y fijar los pesos; la medida honesta es el *test*. Los *ensembles* de doce y ocho miembros aplican la regla anterior.

| Candidato | MAE val. | MAE *test* | Captura *test* | Pico ±1 h |
|---|---:|---:|---:|---:|
| *Ensemble* seleccionado (6) | 11,73 | 11,72 | 92,6 | 76,4 |
| *Ensemble* de todas las familias (12) | 12,63 | 11,88 | 92,9 | 78,3 |
| *Ensemble* de redes y *boosting* horario (8) | 12,52 | 12,03 | 90,5 | 77,4 |
| Denso (mejor individual en validación) | 12,94 | 13,29 | 88,5 | 74,7 |
| LightGBM global | 13,06 | 14,25 | 90,5 | 75,0 |
| GRU (mejor individual en *test*) | 14,13 | 13,19 | 89,6 | 77,0 |
| Persistencia (*naive*) | 19,95 | 16,72 | 86,1 | 67,5 |

*Nota:* MAE en €/MWh; captura del *spread* y acierto de la hora pico en *test*, en %. Individuales: media de tres semillas.

Ningún individual sirve como final: el mejor en validación no es el mejor en *test*. Con el mismo criterio se descartó el ancla semanal descrita más abajo: el *ensemble* seleccionado obtenía con ella 11,83 en validación, frente a 11,73 con ancla D. Quitar un miembro sube ese error en 0,54 (LightGBM global), 0,25 (denso), 0,06 (seq2seq absoluto) o menos de 0,02 (resto). Como matiz de negocio, el seleccionado gana en error, pero queda algo por debajo del de todas las familias en captura y hora pico, sobre todo por los lineales que excluye; entre las redes, el seq2seq absoluto es la que mejor sitúa el pico. El criterio fue el error; el detalle, en el capítulo 5.

## Decisiones transversales

**La copia del día D.** Las curvas de D+1 copiaban en exceso la forma de D sin haber fuga: la salida residual se deshace sumando el precio de D, y lo que el modelo no explica queda cerca de D. Un ancla semanal, D + w·(D−6 − D), con D−6 el precio del mismo día de la semana que D+1 y w ∈ [0, 1] ajustado en entrenamiento por día de la semana y hora, reducía algo la copia, pero no mejoraba la validación del *ensemble* y se descartó. Dos indicadores la miden frente a la realidad: la distancia de la predicción a D y la correlación de su perfil horario, sin nivel, con el de D.

**La frontera de producción.** A las 11:00 de D solo existen, de D+1, previsiones, calendario e indisponibilidades declaradas; una auditoría contrasta cada columna con su fuente. Antes de abril de 2024, sin archivo, la meteorología usa una pseudoprevisión: reanálisis más bloques remuestreados de 24 horas de errores reales de previsión; validación y *test* usan previsiones reales. Se retiraron los retardos del ERA5, publicado con unos cinco días de retraso, y las series de Trayport, y el entrenamiento se detiene si reaparecen. Las previsiones históricas de REE se cargaron ya revisadas; medir su efecto queda pendiente.

## Frente a una búsqueda automática

Una herramienta de AutoML habría optimizado, como esta selección voraz, el error de validación sobre los candidatos dados, pero no los habría diseñado como un experimento controlado ni auditado cada columna, medido la captura del *spread* o diagnosticado la copia del día D o el SARIMAX sin constante, fallos que ese error no delata.
