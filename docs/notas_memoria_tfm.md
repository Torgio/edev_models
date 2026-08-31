# Notas para la memoria del TFM (documento de 20 páginas)

Documento vivo. Aquí se recogen los hallazgos e ideas que el equipo considera relevantes para
el documento final — redactados en lenguaje de negocio, no como hallazgo técnico en crudo. La
evidencia técnica detallada de cada punto vive en el Banco de Evidencias y en el notebook
`01_eda_premodelado.ipynb`; aquí solo va la versión pensada para lectores no necesariamente
técnicos, tal como el equipo acordó enfocar el documento (problema de negocio, no solo método).

Formato del documento final: sin plantilla fija, máximo 20 páginas, foco en el problema de
negocio.

---

## 1. Por qué el modelo necesita "memoria reciente" del mercado, no solo previsiones

**El problema de negocio**: un modelo que solo mira "lo que se prevé para mañana" (demanda,
eólica, solar previstas, calendario) puede fallar precisamente en los momentos en que más
importa acertar — durante episodios prolongados como una ola de calor, una ola de frío, o la
indisponibilidad de una central importante durante varios días. Son justo esos episodios los
que producen los precios más extremos y volátiles del mercado, y por tanto donde un error de
previsión sale más caro.

**El mecanismo**: dos días con calendario y previsiones idénticas sobre el papel pueden tener
precios reales muy distintos si uno de ellos llega después de una semana de mercado anómala y
el otro después de una semana normal. Sin ninguna referencia al pasado reciente, el modelo no
tiene forma de distinguir ambos casos — le daría la misma predicción a los dos, cuando la
realidad es distinta.

**La solución**: incorporar como variables de entrada el precio y la generación **reales** de
los días inmediatamente anteriores (ayer, hace una semana) — lo que en la jerga técnica se
llama "lags". No es información prohibida ni genera fuga: son datos que ya ocurrieron y se
conocen perfectamente en el momento de hacer la predicción.

**Aclaración importante para no confundir en la memoria**: esto no "elimina" la autocorrelación
del precio (que el precio se parezca a sí mismo en el pasado cercano es una propiedad real del
fenómeno, no un defecto que corregir). Lo que hace es dejar que el modelo **aproveche** esa
autocorrelación como información legítima, en vez de ignorarla. Si se ignora, el modelo no
sobreajusta — al contrario, pierde capacidad de distinguir situaciones (subajuste). El riesgo de
sobreajuste no viene de usar estos datos del pasado, sino de usarlos mal — por ejemplo, si por
error se colara información del propio día que se predice (fuga de información), el modelo
parecería funcionar muy bien en las pruebas y fallaría en producción. Todo el trabajo de
catalogar qué variables son seguras y cuáles no (ver Banco de Evidencias) existe precisamente
para que esto no ocurra.

**Implementado (17-ago-2026)**: demanda, eólica/bombeo, solar/termosolar y precio real de D-1 y
D-7 ya están en `dataset_diario` (42 columnas nuevas, de 43 a 85 en total). Se verificó
explícitamente que para la fila más reciente, el lag de "ayer" toma el dato del día anterior y
nunca del propio día que se predice.

---

## 2. El clima como feature tabular — de "sí importa" a "no tanto como pensábamos" (ver actualización del 18-ago)

**El problema de negocio**: el proyecto planea incorporar datos meteorológicos (temperatura,
viento, radiación solar) como variable de entrada, pero el histórico completo (2020-2024) todavía
no está cargado — solo hay disponibles los últimos 19 meses. Antes de invertir el tiempo de cargar
ese histórico completo, el equipo quería saber si merece la pena: ¿el clima realmente ayuda a
predecir el precio, o es una variable más que añade complejidad sin aportar demasiado?

**La prueba**: se entrenó un modelo de referencia (sin datos de clima) y un segundo modelo idéntico
pero con datos meteorológicos añadidos, usando únicamente los 19 meses ya disponibles, y se
compararon ambos sobre el mismo periodo de prueba (los últimos 3 meses, nunca visto durante el
entrenamiento, para simular una predicción real).

**El resultado**: el modelo con clima predice con un error un 10% menor que el modelo sin clima.
Además, al mirar qué variables usa más el modelo, las variables climáticas representan más de un
tercio del peso total de la decisión — a la par con variables ya consolidadas como el precio del
CO2 o la demanda prevista.

**La conclusión**: aunque solo se probó con una fracción de los datos que habrá disponibles a
futuro, la mejora ya es clara y consistente, no una casualidad estadística. Esto justifica
priorizar la carga del histórico meteorológico completo (2020-2024) frente a otras tareas — la
inversión de tiempo tiene un retorno medible y demostrado, no es una apuesta a ciegas.

**Corrección importante (17-ago-2026)**: se detectó y corrigió un error en la construcción del
dataset — el precio objetivo de cada fila no estaba correctamente desplazado al día siguiente
(D+1), sino que por error tomaba el precio del propio día. Tras corregirlo y repetir la prueba, el
resultado se mantiene (mejora de ~10% en ambos casos, antes y después de la corrección) — la
conclusión de que el clima ayuda no cambia, solo las cifras exactas (de +10.3%/+9.5% a
+9.7%/+10.6% en MAE/RMSE). Vale la pena mencionar en el documento final que el equipo tiene un
proceso activo de verificación de resultados, no solo confiar en que el código "corrió sin
errores" — es parte de la rigurosidad metodológica del proyecto.

**Actualización definitiva (18-ago-2026) — el resultado cambia con la serie completa**: al
terminar el backfill de ERA5 2020-2024, se repitió la prueba sobre los 6,5 años completos y con
el reparto oficial de entrenamiento/validación/prueba del equipo (en vez de los 19 meses y el
periodo de prueba corto de antes). Resultado: la mejora prácticamente desaparece (+0,7% en el
periodo de prueba final, e incluso ligeramente peor en el de validación) — el peso de las
variables climáticas cae de un tercio a menos de un 3% de la importancia del modelo.

**Por qué cambia la conclusión, no es una contradicción, es más información**: el histórico
completo incluye la crisis energética de 2021-2022, donde el precio del gas explica el precio de
la luz muchísimo más que el clima — con esos años dentro del entrenamiento, el gas domina por
completo. Además, desde la primera prueba se añadieron variables de "memoria reciente" (el precio
de ayer y de hace una semana, ver nota 1), que probablemente ya capturaban buena parte de lo que
antes parecía aportar el clima de forma indirecta. **Conclusión para el documento final**: la
decisión de completar el backfill fue correcta con la información que había en ese momento — y
confirmar con más datos que el clima aporta menos de lo esperado también es un resultado útil,
no un fracaso: dice que ese esfuerzo no debe ser la prioridad como variable tabular simple: el
clima probablemente valga más para la capa de modelado más avanzada (la que usa directamente los
mapas de viento/temperatura, no solo un promedio diario) que como una columna más en un modelo
tabular como este.

---

## 3. Por qué solo 5 de las ~19 tablas se consideran el "núcleo" del sistema eléctrico

**El problema de negocio**: la base de datos tiene 19 tablas con 271 columnas en total, alimentadas
por fuentes distintas (ENTSO-E, ESIOS, Trayport, mercados de materias primas). No todas sirven para
predecir el precio, y el equipo necesita un criterio claro y defendible de qué datos son realmente
la base del modelo, no una lista arbitraria.

**El criterio**: se consideran "núcleo horario" las tablas que cumplen cuatro condiciones a la vez
— (1) granularidad horaria, (2) histórico completo y continuo desde 2020 hasta hoy (~58.000 filas,
sin huecos estructurales), (3) describen una variable fundamental del sistema eléctrico (generación
real, demanda real, o previsión oficial del día siguiente), y (4) ya se verificó que no duplican
información de otra fuente sin resolver cuál "gana". Con ese criterio quedan cinco:
`entsoe_gen_data` y `esios_gen` (generación real), `entsoe_load_inter` y `esios_load_inter`
(demanda real e intercambios), y `esios_forecast_da` (la previsión oficial que la Circular 4/2019
de la CNMC obliga a publicar antes del cierre del mercado — la única previsión legítimamente segura
como variable de entrada).

**Lo que se queda fuera y por qué, con datos concretos**: `spot_price` tiene la misma cobertura
horaria completa pero se mantiene aparte a propósito — es el objetivo a predecir, no una entrada.
Las tres tablas `esios_pbf_*` (programación de generación/demanda) tienen un volumen de datos
similar al núcleo, pero se excluyen porque llegan estructuralmente tarde (hasta 8 días de retraso),
no porque falten datos. `entsoe_forecast_da` solo tiene 192 filas — prácticamente sin alimentar,
pendiente de conversación con el equipo. El clima (ERA5/ECMWF) se trata aparte porque su historial
es mucho más corto que el resto.

---

## 4. Hoy solo se usa el 16% de las columnas disponibles — y es intencional, no un descuido

**El problema de negocio**: si alguien del equipo (o del tribunal) pregunta "¿por qué el modelo no
usa toda la información que tenemos?", hace falta una respuesta clara, no improvisada.

**El dato**: de las 271 columnas que existen en las 19 tablas de la base de datos, el dataset de
entrenamiento (`dataset_diario`) usa hoy 43 (un 16%). No es que falte trabajo por hacer — cada
exclusión tiene un motivo documentado: variables descartadas por riesgo de fuga de información
(dan un dato que en la práctica no se conoce todavía en el momento de predecir), variables de
tablas que aún no se han incorporado porque su función es servir de "memoria reciente" (los lags,
ver nota 1, todavía no implementados en el dataset final), variables de tablas mal alimentadas o
sin script activo, y variables puramente operativas (identificadores, marcas de última
actualización) que nunca aportan señal predictiva.

**La conclusión**: el 84% que no se usa hoy no es dato desperdiciado — es la evidencia de que cada
variable que sí entra al modelo pasó un filtro explícito de seguridad y calidad. Ese filtro (y no
la cantidad de columnas) es lo que hay que poder explicar si se pregunta.

---

## 5. Qué países vecinos entran al modelo, y por qué Alemania no hace falta aunque sí influye

**El problema de negocio**: España no vive aislada — el precio de la luz en otros países europeos
puede arrastrar el español. Con 10 países disponibles en los datos, había que decidir con criterio
cuáles aportan señal real y cuáles no, en vez de meterlos todos "por si acaso".

**El criterio, en dos pasos**: primero, ¿hay cable físico entre ese país y España? España solo está
conectada eléctricamente a **Portugal** y **Francia** — el resto (Alemania, Italia, Suiza, Bélgica,
Países Bajos, Austria, Polonia, Chequia) no tiene interconexión directa. Segundo, para los que sí
la tienen, se midió cuánto se parecen sus precios al español: Portugal coincide con España el 95%
de las horas (es, a efectos prácticos, el mismo mercado); Francia se parece bastante menos (una
relación real, pero no tan fuerte, porque la capacidad de intercambio entre España y Francia es
limitada frente al tamaño de los dos mercados).

**El caso Alemania — la corrección importante**: un miembro del equipo con experiencia en el
sector energético señaló que Alemania sí tiene fama de mover los precios de toda la península
ibérica, aunque no tenga cable directo. Comprobado con datos: es cierto que el precio alemán
correlaciona con el español — pero esa relación **desaparece casi por completo en cuanto se
descuenta lo que ya explica Francia**. En otras palabras: Alemania mueve el precio francés (es el
mercado más grande e influyente de Europa continental), y Francia mueve el español — pero una vez
el modelo ya tiene el dato de Francia, el de Alemania no añade información nueva, solo la repite
de segunda mano.

**La conclusión**: el modelo incluye el precio real (con al menos un día de retraso, nunca del
propio día que se predice, por la misma razón que el precio español) de Portugal y Francia. No es
que Alemania "no importe" — importa, pero de forma indirecta, y esa influencia ya queda capturada
sin necesidad de añadirla por separado. Es un buen ejemplo de por qué conviene medir la relación
"neta" de cada variable con el precio, no solo la relación simple — evita meter variables
redundantes que dan la sensación de más información sin aportarla de verdad.

---

## 6. Cómo se tratan los eventos extremos — la crisis energética y el apagón no son el mismo caso

**El problema de negocio**: el histórico del proyecto incluye dos episodios muy distintos de lo
normal — la crisis energética europea de 2021-2022 (varios años con el precio del gas disparado) y
el apagón peninsular del 28-abril-2025 (un colapso total de la red, de un día). Meter los dos en el
modelo sin distinguirlos sería un error — pero tratarlos igual también lo sería, porque no son el
mismo tipo de fenómeno.

**Un evento puntual, sin repetición, no se puede aprender — se aparta**: el apagón es un caso
único en seis años de datos. Un modelo no puede aprender un patrón generalizable de un solo
ejemplo — lo único que haría sería memorizarlo, lo cual no sirve para nada fuera de esa fecha
exacta. Por eso ese día (y los que dependen de él por el efecto de arrastre de la "memoria
reciente", ver nota 1) se aparta del entrenamiento y de la evaluación, documentado como lo que es:
un evento real y extremo, no un error de datos.

**Un cambio sostenido, con cientos de días de ejemplo, sí se puede aprender — pero por su causa,
no por su etiqueta**: la crisis del gas duró años, así que hay datos de sobra para que el modelo
aprenda de ella. La forma correcta de hacerlo no es ponerle una etiqueta de "esto fue crisis" —
eso solo enseñaría a memorizar esa crisis concreta, e serviría de poco si en el futuro sube el gas
por un motivo distinto. La forma correcta es la que ya se usa: incluir el precio del gas como
variable de entrada normal, continua. Así el modelo aprende la relación real entre el precio del
gas y el de la luz, que se puede aplicar a cualquier subida futura del gas, la haya visto antes o
no — es la diferencia entre enseñarle al modelo "qué pasó" (fragil, no generaliza) y "por qué pasó"
(un mecanismo, sí generaliza).

**Pendiente de hacer, útil para el capítulo de limitaciones**: el periodo de prueba actual (2026)
no incluye ninguna crisis parecida, así que todavía no se sabe cómo respondería el modelo si
volviera a pasar algo así. Antes de dar el modelo por bueno, conviene evaluarlo también sobre
2021-2022 como prueba de estrés aparte — no como métrica oficial, sino para poder decir con
seguridad qué tan bien (o mal) se comportaría el sistema ante una crisis similar.

## 7. Primer resultado completo de SARIMA — supera tanto a la persistencia como al RandomForest sin afinar

**Fecha**: 19 de agosto de 2026. **Evidencia**: `modelos/validacion_sarima_364d.py`, validación
walk-forward D+1 sobre los 365 días oficiales de validation (2025-01-01 a 2025-12-31, reajustando
el modelo cada día con los 90 días anteriores) — el mismo split que usan el resto de modelos.

**El resultado**: SARIMA(2,0,1)(1,0,1,24) obtiene un error medio de **17,19 €/MWh** (MAE) sobre esos
365 días, frente a **19,90 €/MWh** de la persistencia (copiar el perfil de precio de ayer a hoy) —
una mejora del 13,6%. En error cuadrático (RMSE, que penaliza más los fallos grandes) la mejora es
todavía mayor: 21,36 frente a 25,28 €/MWh, un 15,5% menos. SARIMA gana en 212 de los 365 días
(58,1%) — una ventaja consistente, no aplastante: no es que acierte siempre mejor, es que en
conjunto se equivoca menos.

**El hallazgo que no se esperaba**: para tener un punto de comparación limpio, se entrenó también
un RandomForest por defecto (sin afinar, 300 árboles) sobre exactamente el mismo dataset y el mismo
split. Resultado: **21,85 €/MWh de MAE — peor que la propia persistencia** (19,90). Es decir, con
los datos actuales, un modelo "de fábrica" sin afinar hiperparámetros no aporta nada frente a la
solución más simple posible, y SARIMA — que solo mira la serie de precio, sin ver ni una sola
feature del resto del dataset — le saca una ventaja clara a los dos.

**Por qué tiene sentido, no es un error**: SARIMA se reajusta cada día con el precio real más
reciente (ver nota 1, la autocorrelación a 1 hora es de 0,979) y eso le da una "memoria" muy
directa de lo que acaba de pasar. El RandomForest sin afinar se entrena una sola vez sobre cuatro
años de historia y generaliza peor de lo esperado en el tramo evaluado. Esto no dice que los
modelos de árboles sean peores en general — dice que el afinamiento de hiperparámetros (ya en
marcha con LightGBM/Optuna) no es un lujo, es la diferencia entre superar a SARIMA o no.

**Qué falta para la comparación completa**: este número de SARIMA es ahora el **listón real** a
superar — cualquier modelo de árboles afinado tiene que bajar de 17,19 €/MWh de MAE en esta misma
ventana para justificar su mayor complejidad frente a un SARIMA simple. Falta repetir esta
comparación con XGBoost y LightGBM ya afinados sobre el mismo split exacto para tener el cuadro
completo.

## 8. Por qué `construir_dataset_maestro.py` es una capa "plata" y `bronzeDF_pipeline.py` es "bronce"

**El marco**: en arquitectura de datos por capas (bronce/plata/oro), bronce es el dato crudo tal
cual llega de la fuente, sin opinión ni limpieza — su valor es ser una copia fiel y auditable.
Plata es el dato ya reconciliado: cuando dos fuentes cuentan lo mismo distinto, alguien decidió
cuál es la verdad, con evidencia, y dejó un dato limpio y listo para usar.

**`bronzeDF_pipeline.py` hace exactamente lo que su nombre promete, ni más ni menos**: un
`SELECT *` de cada tabla fuente y las apila todas en un único DataFrame largo, etiquetando de qué
tabla vino cada fila. Cero transformación, cero criterio — y eso está bien para lo que es. El
problema solo aparecería si alguien intentara modelar directamente sobre esa capa sin pasar antes
por una de limpieza. Un dato concreto: a día de hoy ese pipeline referencia `esios_load_inter`,
la tabla que el equipo eliminó el 18 de agosto — fallaría si se ejecutara.

**`construir_dataset_maestro.py` gana la etiqueta "plata" por el tipo de trabajo que hace, no por
tener más código**: elige la fuente ganadora por variable (no por tabla) con evidencia detrás de
cada elección; excluye series contaminadas ya detectadas (autoconsumo, valores falsos de bombeo);
calcula columnas que no existen en ninguna fuente por separado (la FV limpia, resta de dos
fuentes); normaliza fechas de forma segura; y distingue una previsión de un dato real para no
filtrar información del futuro al modelo. Eso es precisamente lo que separa una capa plata de una
bronce: no es "más limpieza", es que alguien tomó decisiones verificables sobre qué versión del
dato es la correcta.

**Matiz para la memoria**: el script en realidad va un paso más allá de plata. Además de
reconciliar fuentes, define el objetivo de predicción (D+1), construye los retardos D-1/D-7 y fija
el split train/validation/test oficial del equipo — trabajo que en el mismo lenguaje se llamaría
capa "oro" o *feature store*: datos formateados para un consumo específico (predecir el precio del
día siguiente), no solo datos limpios de propósito general.

## 9. Ampliación grande del dataset (20-ago-2026) — de 175 a 228 columnas por defecto, alineado a la matriz oficial del equipo

**Qué cambió**: el equipo cerró la matriz de generación (ya existe como vista materializada
`generation` en la base de datos, 16 tecnologías) y confirmó qué columnas de `esios_forecast_da`,
`load_inter` y `commodities` entran al dataset. `construir_dataset_maestro.py` se actualizó para
seguir esa selección punto por punto.

**El cambio más importante no es una columna nueva, es una corrección**: `hydro_reservoir_mw` y
`pumping_gen_mw` se usaban sueltas desde hace semanas, y resultó que `pumping_gen_mw` está vacía el
100% de 2020-2021 y el 91% de 2022 — porque ENTSO-E no separaba la turbinación de bombeo del
embalse antes de diciembre de 2022. Eso significa que `hydro_reservoir_mw` no mide lo mismo antes y
después de esa fecha (su media pasa de 2.756 a 1.720 a 1.940 MW en tres años seguidos) — el mismo
tipo de discontinuidad a mitad de train que ya habíamos encontrado con el autoconsumo, pero esta
vez en una variable que llevábamos usando sin saberlo. Se corrigió fusionando las dos en una sola
columna (`hydro_dispatch_mw = hydro_reservoir_mw + pumping_gen_mw`), que sí es homogénea en los
seis años.

**Decisión de negocio revertida a petición del equipo**: la demanda real vuelve a `ree_load` en vez
de `entsoe_load`, pese a la contaminación de autoconsumo documentada en la nota 3 (D-03). Queda
señalado en el código para quien retome el tema.

**Una columna se dejó fuera a propósito, pendiente de confirmación**: `demanda_residual_prev_mw` no
se añadió pese a estar en la lista del equipo, porque se revisa 10-14 días después de publicarse —
incluirla tal cual sería una fuga de información real (el valor guardado hoy para una fecha pasada
ya está revisado, no es el que existía al momento de predecir), de la misma familia que el bug del
target D+1 corregido el 17 de agosto. Pendiente de una confirmación específica del equipo sobre
ese punto antes de añadirla.

## 10. Cinco familias de columnas quedaron fuera del dataset por decisión de reunión, no por calidad de dato

**El acuerdo**: en la reunión del 20-ago-2026 se decidió que el dataset se limita estrictamente a
las columnas confirmadas en las 4 categorías discutidas (previsión ESIOS, generación, demanda e
interconexiones, commodities) — todo lo demás queda fuera por ahora, aunque el script sepa
construirlo. El detalle completo, columna por columna, está en
`docs/columnas_pendientes_equipo.md`; se puede recuperar todo para explorar con
`construir_dataset_diario(incluir_columnas_pendientes=True)`, sin tocar código.

**Lo importante para la memoria**: dos de las cinco familias excluidas no son un recorte neutro.
El **precio de Portugal** (correlación 0,997 con España — prácticamente el mismo mercado) y la
**capacidad disponible** (la variable que mejor explica los picos de precio por paradas de
nucleares, ver nota de decisión D-01) ya estaban señaladas como dos de las señales más fuertes de
todo el catálogo, antes incluso de esta reunión. Dejarlas fuera "por ahora" tiene un costo
esperado real sobre el error del modelo, no solo una simplificación de alcance — vale la pena que
quede explícito en la memoria como una decisión consciente del equipo, con su compensación
(alcance más controlado, más fácil de auditar) frente a su costo (se deja fuera señal ya
verificada como fuerte).

## 11. El clima (ERA5) entra al dataset por defecto — con visto bueno del equipo, el 20-ago-2026

El equipo aprobó explícitamente incluir la categoría METEO en `construir_dataset_maestro.py`.
`_features_clima` ya existía desde antes (ver nota 2) pero estaba apagada por defecto —
`construir_dataset_diario(incluir_clima=True)` era necesario para activarla. Ahora `incluir_clima`
es `True` por defecto; sigue pudiéndose desactivar con `incluir_clima=False`.

**La salvedad de siempre sigue siendo válida y vale la pena repetirla aquí**: ERA5 es reanálisis
—clima que ya ocurrió, medido después del hecho—, no una previsión. Usarlo para predecir D+1
asume que en producción existiría una previsión meteorológica (ECMWF) igual de buena para D+1, lo
cual todavía no está validado (`ecmwf_forecast_agg`, la tabla de previsión real, apenas tiene unos
días de histórico). El equipo decidió aceptar esa asunción por ahora — es una decisión consciente,
no un descuido, y conviene que quede así de explícito en la memoria: el dataset de entrenamiento
usa clima "perfecto" (el que realmente ocurrió), mientras que en producción real se dependería de
una previsión, que nunca es perfecta. Esa diferencia es una fuente de optimismo en la métrica de
validación que hay que mencionar en el capítulo de limitaciones.

**Ampliado el mismo día a las 9 columnas completas** (antes 6): se añaden `d2m_mean` (punto de
rocío, convertida de Kelvin a Celsius igual que la temperatura), `wind_gust10_mean` (racha de
viento) y `msl_mean` (presión a nivel del mar). Quedan fuera `tensor_path`/`tensor_index` de
`era5_weather_agg` — son metadatos internos del pipeline de descarga (ruta y índice del tensor
NetCDF), no variables físicas del clima.

**Pendiente de conversación con el equipo, palabras del propio compañero de energía**: qué hacer
respecto a `ecmwf_forecast_agg` (la previsión real, hoy con solo ~13 días de histórico) — es la
pieza que, cuando acumule suficiente historia, resolvería la salvedad de "clima perfecto vs.
previsión real" señalada arriba.

## 12. Segundo resultado completo: la curación de columnas no perjudicó al modelo, y LightGBM sin afinar ya supera a la persistencia

**La pregunta**: después de todos los cambios de columnas (notas 9-11), ¿la selección quedó mejor
o peor que antes? Se repitió la comparación de RandomForest sobre el dataset nuevo (261 columnas)
con exactamente la misma metodología que en la nota 7, y se completó el cuadro con XGBoost y
LightGBM sin afinar:

| Modelo | Antes (256 col.) | Ahora (261 col.) |
|---|---|---|
| Persistencia | MAE 19,88 | MAE 19,88 (sin cambio, no usa las features) |
| RandomForest sin afinar | MAE 21,85 | **MAE 21,43** (mejora) |
| XGBoost sin afinar | — | MAE 19,79 |
| LightGBM sin afinar | — | **MAE 18,23** |
| SARIMA (referencia, univariante) | MAE 17,19 | MAE 17,19 (sin cambio) |

**Dos conclusiones**: primera, la curación de columnas (sacar las 4 familias pendientes, sumar
`entsoe_load` y el clima completo) mejoró al RandomForest en vez de perjudicarlo — la selección
más cuidadosa no costó capacidad predictiva. Segunda, y más importante: **LightGBM sin ningún
afinamiento de hiperparámetros ya supera a la persistencia** (18,23 vs. 19,88) y queda a solo 1,04
€/MWh de SARIMA — a diferencia de RandomForest, que seguía perdiendo contra la persistencia incluso
con el dataset nuevo. Esto es evidencia de que el problema no era "los árboles no sirven", como
podía sugerir la nota 7 — era que RandomForest específicamente necesita afinamiento para esta
tarea, mientras que LightGBM ya es competitivo de fábrica.

**En marcha**: campaña de afinamiento de LightGBM con Optuna (300 trials) sobre este dataset nuevo,
para ver cuánto se puede acercar o superar a SARIMA con el hiperparámetro correcto.

## 13. Dos hallazgos de calidad de dato, confirmados por dos vías independientes el mismo día

**Contexto llamativo**: el mismo 21 de agosto, un compañero de energía señaló en el chat del
equipo dos problemas de convención NULL/0 en la generación real — y, sin coordinación, otro
compañero (rama de bronce) documentó exactamente los mismos dos problemas en su propio EDA
(`notebooks/02_eda_bronze.ipynb`, PR #10). Que dos personas distintas, por dos caminos distintos,
lleguen al mismo hallazgo el mismo día es una señal fuerte de que ambos son reales, no ruido.

**`pumping_gen_mw` (turbinación de bombeo) — ya estaba resuelto, sin saberlo**: la fuente guarda
`NULL` cuando no hubo turbinación (43,97% de las horas), mientras que `pumping_cons_mw` usa `0`
explícito para el mismo tipo de ausencia — dos convenciones distintas para "no pasó nada". Si se
agregara con `.agg(["mean","min","max"])` sin tratar antes ese `NULL`, la media diaria saldría
inflada (calculada solo sobre las horas con turbinación real) y el mínimo real de 0 quedaría
oculto. Se verificó que esto **no afecta al dataset actual**: desde la fusión de `hydro_dispatch_mw`
(nota 9), `pumping_gen_mw` nunca se agrega suelta — solo dentro de
`hydro_reservoir_mw + COALESCE(pumping_gen_mw, 0)`, que ya trata el `NULL` como cero antes de
agregar. La corrección llegó por otro motivo (el escalón de nivel de `hydro_reservoir_mw`) y de
paso resolvió este segundo problema sin que se hubiera planteado explícitamente en su momento.

**`entsoe_load` en 0 exacto — real, y con impacto grande**: 9 horas de 58.151 con demanda
peninsular de 0 MW, físicamente imposible. 8 seguidas (1-jul-2026, madrugada) más 1 aislada
(17-mar-2026) — un corte de ingesta puntual, no un problema sistémico, pero caen en el tramo de
test. Corregido pasando esos ceros a valor ausente antes de agregar, para que el promedio diario
los ignore en vez de contarlos como demanda real. **El impacto medido fue mayor de lo esperado**:
la media del 1-jul-2026 pasaba de 22.381 MW (con el error) a 33.572 MW (corregida) — una
subestimación de un tercio del valor real, en un día que cae justo en la ventana con la que se
evalúan los modelos.

**Cambio adicional, a petición del equipo**: todas las columnas numéricas del dataset se redondean
ahora a 2 decimales al final de `construir_dataset_diario()` — solo presentación, no cambia
ninguna decisión de modelado.

## 14. Qué explica al modelo ganador, y dónde falla — importancia de features y análisis de residuos

**Contexto**: tras el afinamiento de LightGBM (nota 12, mejor MAE 16,62 €/MWh), se rescató el
modelo ganador — reentrenado, guardado en disco (`modelos/artefactos/lightgbm_final.joblib`) junto
con su lista de features y las medianas de imputación, para no depender de volver a correr Optuna
cada vez que alguien quiera usarlo. MAE confirmado al reentrenar: 16,75 €/MWh (pequeña variación
normal frente al 16,62 registrado durante la búsqueda, por el componente aleatorio de `subsample`).

**Qué explica el precio, según el propio modelo — y encaja con lo que ya sabíamos por otras vías**:
las tres primeras posiciones del ranking de importancia son `gas_mibgas`, el lag del propio precio
(`precio_real_max_lag1d`) y la previsión de renovables. El resto del top 15 está dominado por
precios de combustibles y CO2 (`gas_ttf`, `carbon_api2`, `co2_eua_dec`) y por los lags de precio
propio y previsiones de demanda/eólica. Es la confirmación cuantitativa, desde dentro del modelo,
de algo que se había argumentado cualitativamente toda la sesión: el gas fija el precio marginal
vía el ciclo combinado, y la autocorrelación del precio es la segunda señal más fuerte. Ningún
hallazgo nuevo aquí — pero es la primera vez que se mide directamente en vez de inferirse.

**Dónde falla el modelo — y es exactamente donde se esperaría**: el peor día de todo el año de
validation es el **28 de abril de 2025** (MAE 53,51 €/MWh, más del triple del promedio) — el día
del apagón, marcado como evento extremo. El MAE medio en los 6 días marcados como evento extremo es
**21,91 €/MWh, un 31% peor** que en los 2.404 días normales (16,66). Esto confirma con evidencia
propia lo que ya se había decidido por lógica de negocio (nota 6): un evento puntual sin repetición
es exactamente lo que un modelo no puede aprender a predecir bien, por bueno que sea en el resto.
Dato curioso: el modelo predice ligeramente mejor los fines de semana (MAE 16,11) que entre semana
(17,00) — consistente con una demanda de fin de semana más baja y menos volátil.

**Implicación para la siguiente capa de arquitectura**: con el error ya concentrado de forma tan
clara en un puñado de eventos atípicos y no distribuido de forma difusa en el tiempo, no hay
evidencia todavía de que una arquitectura secuencial (CNN-LSTM/Transformer) fuera a capturar algo
que LightGBM se esté perdiendo por falta de memoria temporal — el problema parece más de "eventos
raros e impredecibles" que de "estructura temporal no capturada". Vale la pena tenerlo en cuenta
antes de invertir en la siguiente capa.

## 15. Cambio de estructura: una fila por hora en vez de una fila por día — mejora grande, no marginal

**La idea, propuesta en conversación con un compañero**: en vez de que cada fila sea un día
completo (con 24 columnas de destino y las features comprimidas a media/mín/máx), que cada fila
sea una hora concreta de D+1, con una sola columna de destino y la hora del día como variable más.
Implementado en paralelo, sin tocar `construir_dataset_maestro.py`, en
`modelos/construir_dataset_horario.py` — mismo catálogo de columnas, mismas fronteras de split,
solo cambia la estructura de filas.

**Resultado de la primera prueba** (LightGBM sin afinar, mismos hiperparámetros en los dos
formatos, para que la comparación sea limpia):

| Formato | MAE |
|---|---|
| Diario (24 modelos independientes vía MultiOutputRegressor) | 18,23 |
| **Horario (1 solo modelo, "hora" como variable)** | **13,75** |

Una mejora del 24,6% — y este resultado sin afinar **ya supera al mejor LightGBM diario afinado
con 313 trials de Optuna (16,62)**. Es la mejora más grande de toda la sesión, muy por encima de
lo que aportó cualquier ajuste de columnas.

**Por qué tiene sentido, no es un artefacto**: se revisó específicamente que no hubiera fuga de
información nueva (los lags de 24h/168h solo miran al pasado, las features seguras siguen
describiendo la misma hora que antes, solo que unidas por timestamp exacto en vez de agregadas por
día). La mejora parece genuina y viene de dos sitios: el modelo entrena con 24 veces más filas
(43.866 horas en vez de 1.822 días) porque ya no hace falta partir el aprendizaje en 24 modelos
aislados, y cada fila lleva el valor de previsión exacto de esa hora en vez de un resumen del día
completo — información que el formato diario tiraba a la basura.

**Hallazgo colateral de método**: al construir el dataset horario se descubrió que `era5_weather_agg`
es nativa cada 3 horas, no horaria — al pasar a formato horario, dos de cada tres horas de clima
quedaban vacías "de fábrica" (algo que el promedio diario disimulaba por completo). Se corrigió con
interpolación acotada a 2 horas, el tamaño exacto del hueco.

**Pendiente**: falta reentrenar el resto del catálogo (SARIMA no aplica igual porque ya trabaja
sobre la serie horaria nativa; XGBoost, RandomForest, y el propio LightGBM afinado con Optuna)
sobre esta estructura antes de decidir si reemplaza al dataset diario o conviven ambos.

## 16. Cambios de esquema del 22-ago-2026: capacidad por tecnología, autoconsumo previsto, y un experimento de fuga con PDBC

**El equipo actualizó varias tablas** (verificado en vivo, no solo por la captura compartida):
- `esios_capacity_available` pasó de diaria+agregada a **horaria y por tecnología** (hydro, bombeo,
  nuclear, carbón antracita, CCGT, fuel) — exactamente el desglose que la decisión D-01 dejaba
  pendiente. Se corrigió `_features_capacidad_disponible`, que apuntaba a las columnas viejas
  (`date`/`total_mw`, ya no existen) y habría fallado si alguien la activaba.
- Nueva vista `forecast` con dos columnas de autoconsumo previsto (`c_autoconsumo_prev`,
  `autoconsumo_estimado`) que no existían antes — añadidas como evaluación.
- Nueva tabla `esios_pdbc_gen` (programa de generación del PDBC) — ver el experimento crítico
  abajo.
- `spot_price` confirmado con 13 países — se amplió `COLS_PRECIO_VECINOS` a los 10 sin
  interconexión física (antes solo Portugal/Francia), a petición del equipo, "para probar".
- Por instrucción del equipo, se retiraron `co2_ets`, `gas_ttf` y `carbon_api2` de
  `COLS_COMMODITIES` — **pese a que la nota 14 (importancia de features) las tenía en las
  posiciones #6 y #8 de 237 variables**. Aplicado tal como se pidió, dejando constancia de la
  contradicción para que el equipo lo revise con esa evidencia sobre la mesa.

**Experimento crítico: ¿es seguro usar PDBC?** PDBC es el resultado de la MISMA subasta que fija
el precio de D+1 — no una previsión independiente. Se probó en dos modos sobre LightGBM sin
afinar: con lag 24h/168h (tratamiento conservador, igual que el resto de reales) dio una mejora
legítima de 0,63 €/MWh (13,66 → 13,03). Usarlo del mismo día sin desplazar mejoró más (12,08),
pero las columnas de esa versión se llevaron el 26,3% de la importancia total del modelo — señal
clara de que la mejora, aunque no descabellada en magnitud, viene de usar información que en
producción real nunca estaría disponible antes de fijar el propio precio. **Recomendación: usar
PDBC solo con lag, nunca del mismo día**, misma familia de problema que ya se evitó con los datos
de PBF.

**Resultado tras sumar todas las adiciones de esta nota**: LightGBM horario sin afinar bajó a
13,03 €/MWh (desde 13,75 antes de estos cambios) — la curación siguió sumando, no restando.

## 17. Tercer resultado completo: LightGBM horario afinado, el mejor modelo del proyecto hasta ahora

Campaña de Optuna (300 trials) sobre el dataset horario con todas las adiciones de la nota 16.
Resultado: **MAE 12,55 €/MWh, RMSE 16,56** — hiperparámetros ganadores `n_estimators=1900,
num_leaves=27, max_depth=10, learning_rate=0,017, subsample=0,60, colsample_bytree=0,77,
reg_alpha=0,0011, reg_lambda=0,023, min_child_samples=8`.

| Modelo | MAE |
|---|---|
| **LightGBM horario, afinado** | **12,55** |
| LightGBM horario, sin afinar | 13,03 |
| LightGBM diario, afinado (313 trials) | 16,62 |
| SARIMA | 17,19 |
| LightGBM diario, sin afinar | 18,23 |
| Persistencia | 19,88 |

**El hallazgo metodológico más importante de esta comparación**: el afinamiento de hiperparámetros
aportó 0,48 €/MWh (3,7%) — mucho menos que el salto que dio solo cambiar la estructura del dataset
de diaria a horaria (5,20 €/MWh, un 28,5%). La decisión de cómo estructurar las filas del dataset
importó más para el resultado final que 300 trials de búsqueda bayesiana de hiperparámetros. Vale
la pena que esto quede explícito en la memoria: no toda mejora viene de "afinar más", a veces viene
de replantear cómo se le presentan los datos al modelo.

**Es, con diferencia, el mejor modelo del proyecto**: 27% mejor que SARIMA, 24,5% mejor que el
mejor LightGBM diario.

**Pendiente**: probar XGBoost y RandomForest sobre el formato horario para completar el cuadro de
la capa 2. SARIMA no necesita repetirse — ya trabaja nativamente sobre la serie horaria.

---

## 18. Rescate del modelo horario ganador — un patrón de importancia distinto al diario

Igual que con el LightGBM diario (nota 14), se reentrenó el modelo horario ganador (12,55 €/MWh)
con sus hiperparámetros finales y se guardó en `modelos/artefactos/lightgbm_horario_final.joblib`,
junto con su importancia de features y el análisis de residuos.

**El ranking de importancia cambia bastante respecto al modelo diario**: aquí `gen_renovables_prev_mw`
es la variable más importante (en el diario ni entraba al top 15), y **cuatro de las quince
variables más importantes son de eólica** (`gen_wind_prev_mw`, `pdbc_wind_mw_lag24h`,
`wind_mw_lag24h`, `entsoe_wind_forecast_mw`) — el formato horario parece explotar mucho mejor la
volatilidad hora a hora del viento, algo que el promedio diario disolvía. El precio de Portugal
(`pt_entsoe_lag24h`) entra al top 5 pese a estar fuera del dataset diario por defecto — otro dato a
favor de reincorporarlo cuando el equipo lo discuta. El día de la semana (`dow`) y la presión
atmosférica (`msl_mean`) también entran al top 15, confirmando que el calendario y el clima
aportan señal real en esta estructura.

**El error tiene una forma horaria muy clara**: mínimo de madrugada (9,52 €/MWh a las 23h) y
máximo a media mañana (14,83 €/MWh a las 8h) — justo las horas de rampa solar y arranque de
demanda, las más volátiles del día. Fin de semana vuelve a ser más fácil que entre semana (10,81
vs. 13,24), mismo patrón que el modelo diario. Las horas de días marcados como evento extremo
tienen un MAE 50% peor que las normales (18,66 vs. 12,44) — una brecha más marcada que en el
modelo diario (31%), consistente con que el formato horario capta el apagón con más detalle.

## 19. Validación de múltiples ventanas — el resultado es robusto desde 2023, pero 2022 revela un límite real

Para comprobar si la ventaja del formato horario (nota 15) era consistente y no una casualidad del
año de validation oficial (2025), se repitió el mismo entrenamiento (mismos hiperparámetros
ganadores) sobre varias ventanas dentro de 2020-2025, sin tocar test en ningún momento:

| Ventana de validation | Entrenado con | MAE LightGBM | MAE persistencia |
|---|---|---|---|
| 2022 | Solo 2020-2021 | **46,70** | 27,63 (gana la persistencia) |
| 2023 | 2020-2022 | 14,59 | 20,63 |
| 2024 | 2020-2023 | 13,56 | 18,28 |
| 2025 (oficial) | 2020-2024 | 12,55 | 19,86 |

**Desde 2023 en adelante el resultado es consistente y mejora de forma monótona** conforme crece
el histórico — buena evidencia de que el 12,55 de la ventana oficial no es casualidad, sino la
continuación de una tendencia real.

**La ventana de 2022 es un fracaso real y explicable, no ruido**: entrenar con solo 2020-2021 y
validar contra 2022 pierde contra la persistencia por un margen amplio. 2022 fue el pico de la
crisis energética europea — con solo dos años de entrenamiento, el modelo apenas había visto ese
régimen de precios antes de tener que predecirlo. Con tres años de entrenamiento (incluyendo ya el
grueso de la crisis) el modelo pasa a ganar con holgura. **Implicación para el diseño**: la
decisión de entrenar con la ventana completa 2020-2024 (en vez de una ventana más corta y
"reciente") no es solo una convención — los años de crisis dentro del entrenamiento son parte de
por qué el modelo generaliza bien después. Reducir la ventana de entrenamiento sin cuidado podría
reintroducir este mismo problema.

## 20. Primera capa de incertidumbre — el intervalo reacciona en la dirección correcta, pero no lo suficiente

**La decisión de arquitectura**: en vez de construir directamente una capa CNN-LSTM/Transformer,
se priorizó una capa de incertidumbre — la evidencia de las notas 14, 18 y 19 apunta a que el
problema no es falta de estructura temporal capturada (el modelo puntual ya funciona muy bien en
condiciones normales), sino que el modelo no tiene forma de señalar cuándo está en terreno
peligroso. Implementado como regresión cuantílica con LightGBM (`objective="quantile"`, p10/p50/p90),
reutilizando los mismos hiperparámetros ganadores del modelo puntual.

**Resultado**: el intervalo [p10, p90] cubre el 70,8% de los casos (objetivo: ~80%) — está mal
calibrado, por debajo de lo esperado. Y el hallazgo más importante: la cobertura cae de 71,0% en
horas normales a **59,7% en horas de evento extremo**, pese a que el intervalo sí se ensancha algo
en esas horas (+9,7%). **El modelo reconoce parcialmente que esas horas son más inciertas, pero no
lo suficiente** — está sobreconfiado justo donde más importa no estarlo. Es un resultado honesto,
no un fracaso: confirma que vale la pena seguir por este camino, pero que la calibración cruda de
cuantiles no basta.

**Esto mismo se puede ver, no solo en números, en las ventanas de tiempo de
`notebooks/04_incertidumbre_calibracion.ipynb`** (gráficas 1 y 2, panel "sin calibrar" — esas
gráficas usan una corrida posterior del mismo experimento, por eso las cifras exactas varían un
poco frente a las de arriba: 70,3%/70,5%/56,3% en vez de 70,8%/71,0%/59,7%, pero cuentan
exactamente la misma historia). En la ventana de un mes normal (junio 2025), la banda sin calibrar
sigue de cerca al precio real casi todo el tiempo — el intervalo parece funcionar bien. La
diferencia se ve al hacer zoom sobre el apagón del 28-29 de abril de 2025: ahí la misma banda sin
calibrar se queda claramente corta cuando el precio real se dispara — de las 63 horas alrededor
del apagón marcadas como evento extremo, el intervalo crudo solo contuvo el precio real el **59%**
de las veces. Es la forma más directa de mostrarle a alguien qué significa "sobreconfiado justo
donde más importa no estarlo": un mes cualquiera parece estar bien resuelto, y es precisamente el
caso más difícil — el que de verdad importa para una batería o para gestionar riesgo — donde el
intervalo falla más.

**Siguiente paso natural**: calibración conforme (*conformal prediction*), que ajusta el ancho del
intervalo contra un conjunto de calibración para garantizar la cobertura objetivo de forma
empírica, en vez de confiar en que el cuantil entrenado ya esté bien calibrado.

## 21. Calibración conforme — cierra casi toda la brecha de cobertura, pero no del todo en eventos extremos

Implementada la calibración conforme (CQR) sugerida en la nota 20, reutilizando los modelos p10/p90
ya entrenados (sin reentrenar nada) — una sola corrección aditiva (+2,90 €/MWh a cada lado del
intervalo), calculada sobre la mitad de validation y verificada sobre la otra mitad, nunca vista
durante la calibración.

| | Sin calibrar | Con calibración conforme |
|---|---|---|
| Cobertura global | 70,3% | **79,3%** (objetivo 80%) |
| Cobertura en horas normales | 70,5% | 79,4% |
| Cobertura en horas de evento extremo | 56,3% | **71,8%** |

**La cobertura global queda prácticamente resuelta** (70,3% → 79,3%, casi exacto al 80% objetivo),
a costa de un intervalo un 16% más ancho (35,92 → 41,71 €/MWh) — el precio esperado de una mejor
calibración.

**En eventos extremos la mejora es grande (+15,5 puntos) pero incompleta**: queda en 71,8%, todavía
por debajo del 79,4% de las horas normales. Es un resultado honesto: una corrección uniforme (la
misma para todo el dataset) no logra igualar del todo la dificultad de un evento extremo con la de
un día normal. **Refinamiento pendiente**: calibración condicionada al contexto — ensanchar el
intervalo de forma dinámica cuando el modelo detecta señales de régimen atípico, en vez de aplicar
la misma corrección siempre.

## 22. Prueba de estrés: crisis energética 2021-2022 — cierra el capítulo de la capa de incertidumbre

**La pregunta**: si el modelo nunca hubiera visto un período de crisis como el de 2021-2022,
¿qué tan mal lo haría al enfrentarse a uno? Gráficas completas en
`notebooks/05_prueba_estres_crisis.ipynb`.

**Nota de método, por transparencia**: el primer intento de esta prueba entrenó con 2020-2024 (que
YA incluye la crisis) y evaluó sobre 2021-2022 — un error, porque el modelo estaba viendo datos
que ya conocía (dio un MAE de 5,66, engañosamente bueno). La prueba correcta excluye la crisis por
completo del entrenamiento (2020 + 2023-2024) y evalúa solo sobre 2021-2022, nunca visto.

**Resultado real**:

| | MAE |
|---|---|
| Modelo **sin exposición** a la crisis | **38,74** |
| Persistencia (naive D-24h) | 21,88 |
| Modelo con exposición completa (el que usamos hoy, año normal) | 12,55 |

**El modelo sin exposición pierde contra la persistencia, y empeora conforme la crisis avanza**
(MAE 28,61 en 2021 → 48,88 en 2022). La gráfica de precio diario lo muestra con claridad: el
modelo sigue bien el precio real hasta que este empieza a dispararse (octubre 2021 en adelante) —
ahí el modelo "se aplana" cerca de 150 €/MWh mientras el precio real llega a superar los 500,
porque nunca aprendió que el precio pudiera llegar tan alto. La persistencia, en cambio, sigue el
precio real de cerca durante toda la crisis — no porque "entienda" la crisis, sino porque copiar
el valor de ayer funciona razonablemente bien cuando el precio sube de forma gradual y sostenida.

**Conclusión para la memoria**: esto no invalida el modelo actual — el que usamos en producción
SÍ incluye 2021-2022 en su entrenamiento, por eso funciona bien. Lo que confirma es que **la
robustez ante crisis no es una propiedad automática de la arquitectura, depende directamente de
haber visto ejemplos representativos del régimen durante el entrenamiento.** Implicación práctica
y concreta: si en el futuro se acorta la ventana de entrenamiento (por ejemplo, para usar datos
"más recientes"), hay que asegurarse de que la ventana siga incluyendo al menos un episodio de
estrés de este tipo, o el sistema quedaría expuesto exactamente al mismo fallo.

**Con esto se cierra el capítulo de la capa de incertidumbre** (baseline → GBM → incertidumbre,
notas 12-22): modelo puntual fuerte y validado en múltiples ventanas, capa de incertidumbre
calibrada de forma conforme, y ahora una prueba de estrés que delimita con precisión sus límites
conocidos.

---

## 23. Contraste con el trabajo independiente de un compañero de equipo (carpeta `dt_maestro_sergio`)

Un compañero de equipo desarrolló, en paralelo y sin coordinación previa, su propio script de
construcción de dataset y sus propios modelos (LightGBM, MLP, LSTM, y un Seq2Seq encoder-decoder).
Se revisó su código y sus resultados directamente (no solo lo que se comentó de palabra) para
validar qué es aprovechable y qué corregir en nuestro propio trabajo.

**El hallazgo más importante es una coincidencia, no una novedad**: de forma independiente, llegó
a la misma conclusión estructural que nosotros — predecir "una fila por hora" en vez de "una fila
por día". Esto confirma con una segunda fuente que ese cambio de arquitectura (documentado en las
notas 6-8) era el correcto, y no una elección arbitraria de nuestro lado.

**Comparación de resultados — con una salvedad importante de honestidad metodológica**: sus
mejores resultados (Ensemble ~13,0-13,7 €/MWh de MAE, LightGBM 14,3-14,8, Seq2Seq 14,4-15,8, según
la variante) están medidos sobre el conjunto de **test** (2026). Nuestro mejor resultado (LightGBM
horario afinado, MAE 12,55) está medido sobre **validation** (2025) — el test todavía no se ha
tocado, correctamente, porque es la última palabra y solo debe consultarse una vez. Los dos
proyectos usan, por coincidencia, las mismas fronteras de fecha (entrenamiento hasta 2024,
validación 2025, test 2026 en adelante), así que en cuanto decidamos como equipo consultar
nuestro test, la comparación será directa. **Hasta entonces, decir que "vamos ganando" sería
prematuro** — son números de conjuntos distintos.

**Sobre la afirmación "añadiendo ERA5 miren cómo ha mejorado"**: se verificó comparando sus propias
versiones con y sin la meteorología ampliada. La mejora es real pero **más matizada** de lo que
suena la frase — el modelo LightGBM y el Seq2Seq mejoran de forma clara, pero el resultado del
Ensemble (el mejor global) prácticamente no cambia (13,01 → 13,10, una diferencia dentro del
ruido). Es una mejora real en piezas concretas del sistema, no un salto general. Lección para nuestra
propia comunicación de resultados: cuando se reporte una mejora, conviene decir a qué modelo
concreto afecta, no dar la impresión de que sube todo el sistema por igual.

**Sobre la ablación de las columnas NTC (capacidad de interconexión prevista)**: su equipo comparó
dos alternativas — quitar las 6 columnas de NTC previsto (que no tienen datos hasta noviembre de
2020) contra recortar el entrenamiento para empezar en noviembre de 2020 y sí incluirlas. La
diferencia entre ambas resultó estar dentro del margen de error estadístico esperable para el
tamaño de su conjunto de validación (calculan un margen de ±1,1 €/MWh para 339 días), así que
concluyeron, con honestidad, que **la ablación no encontró una diferencia real** y se quedaron con
la opción que ya tenían acordada en equipo. Es un estándar de rigor que vale la pena copiar: antes
de afirmar que un cambio "mejoró" o "empeoró" el modelo, calcular si esa diferencia es mayor que el
margen de error esperable por el tamaño de la muestra de validación, no solo comparar los dos
números a simple vista.

**Sobre `https://forecastscore.eu/`**: es un enlace que le compartió un conocido externo, no se
verificó su contenido en esta revisión (no se navegó al sitio). Pendiente: alguien del equipo debe
revisarlo y contar qué es antes de darle peso en la memoria.

**Dos aportes suyos que vale la pena adoptar en nuestro propio modelo**:

1. **Métricas de valor económico, no solo de precisión.** Además del MAE, mide qué porcentaje del
   "arbitraje perfecto" del día (comprar en la hora más barata, vender en la más cara) captura el
   modelo usando sus propias horas predichas, y si acierta la hora de precio máximo dentro de
   ±1 hora. Encontraron algo importante para nuestro capítulo de baterías: su Seq2Seq tiene *peor*
   MAE que su LightGBM (14,39 vs. 14,24) pero *mejor* valor económico (captura 92% del arbitraje
   frente a 87%, acierta la hora pico el 83% de las veces frente al 76%) — es decir, **el modelo
   más preciso en promedio no es necesariamente el más útil para operar una batería.** Deberíamos
   incorporar estas dos métricas a nuestra propia evaluación antes de decidir qué modelo pasa a la
   capa de baterías.
2. **Tres columnas nuevas, baratas de construir, que nosotros no tenemos**: un indicador de "día
   puente" (entre festivo y fin de semana), un indicador de "víspera de festivo", y un indicador
   del período en que estuvo vigente el mecanismo ibérico de tope al gas (que cambió las reglas del
   mercado durante 2022-2023). Registradas para discusión en
   `docs/columnas_pendientes_equipo.md`.

**Qué falta corregir de nuestro propio lado, en concreto**:

- Acordar con el equipo *cuándo* se consulta el test compartido, para poder comparar ambos modelos
  de forma justa una sola vez, en vez de dejarlo indefinido.
- Adoptar las métricas de captura de arbitraje y acierto de hora pico en nuestra propia evaluación
  (afecta directamente al capítulo de baterías que está pendiente).
- De paso, se encontró y corregido un error real sin relación con los modelos: el archivo
  `.gitignore` tenía marcadores de conflicto de Git (`<<<<<<<`, `=======`, `>>>>>>>`) guardados
  literalmente como texto desde un merge anterior mal resuelto — ya corregido.

---

## 24. Tres acciones de limpieza concretas (no "limpiar a ciegas") y decisión de prioridad: BESS antes que Seq2Seq/CNN-LSTM

Sobre los tres problemas de calidad de datos identificados anteriormente (batería con nulos
masivos, columnas redundantes, NTC Marruecos), se acordó actuar de forma deliberada en vez de
aplicar una limpieza genérica — cada problema es distinto y merece un tratamiento distinto.

**1. Indicador de disponibilidad para las columnas de batería** (`ree_gbattery_mw`,
`ree_cbattery_mw`, 99,3% de nulos en train): en vez de solo rellenar con la mediana, se añadió una
columna binaria (`_disponible`) que dice si esa hora tenía dato real o va a ser imputada — así el
modelo puede aprender a no tratar el valor imputado como si fuera una lectura real. **Hallazgo
importante al implementarlo**: el porcentaje de disponibilidad no es un ruido aleatorio, es un
salto de régimen limpio — 0,7% disponible en train (hasta 2024), 98-100% disponible en validation
y test (2025 en adelante). Las baterías de red entraron en operación real hace muy poco. Esto
significa que el indicador es correcto y necesario, pero **no hay que esperar mucho de la señal
de batería en sí** todavía: el modelo apenas tiene ejemplos de entrenamiento donde la batería
tenga un valor real distinto de "no existe", así que no puede aprender bien cómo afecta al precio
cuando sí existe. Es una limitación real de los datos, no del método de imputación.

**2. Experimento controlado de redundancia**: se recalculó la matriz de correlación sobre el
dataset horario actual (creció bastante desde el análisis original: ahora salen 76 parejas por
encima de 0,93, no 4 — la mayoría son parejas físicamente esperables, como países vecinos
acoplados entre sí por el mercado europeo). Se seleccionaron las 4 parejas más claramente
redundantes en el sentido correcto (dos fuentes midiendo la misma magnitud: ESIOS vs ENTSO-E) y se
entrenó el mismo LightGBM quitando una columna de cada pareja, comparando el MAE de validation con
el error estándar de la diferencia (bootstrap), igual estándar de rigor que usó el compañero en su
propia ablación (nota 23).

**Resultado, y no es el esperado**: de las 4, solo una (`entsoe_solar_forecast_mw`, frente a
`gen_solar_pv_prev_mw`) resultó realmente redundante — quitarla no cambia el MAE de forma
significativa. Las otras tres SÍ importan, aunque el efecto es pequeño en términos absolutos
(entre 0,02 y 0,12 €/MWh sobre un MAE de ~12,8): quitar `entsoe_load_forecast_mw` empeora el
modelo, y quitar `entsoe_load` o `entsoe_wind_forecast_mw` lo mejora ligeramente. **Conclusión
práctica**: una correlación alta entre dos columnas no implica que sean intercambiables para el
modelo — antes de quitar una "por redundante" hay que probarlo, tal como se hizo aquí, no asumirlo
por la correlación. **Hallazgo colateral que hay que resolver aparte**: estas 3 columnas de
ENTSO-E están excluidas por defecto del dataset DIARIO (decisión del equipo, ver
`docs/columnas_pendientes_equipo.md` punto 1) pero se incluyen SIEMPRE en el dataset HORARIO, sin
el mismo filtro — una inconsistencia entre los dos scripts que conviene resolver como equipo,
sobre todo porque este experimento sugiere que al menos una de ellas sí aporta valor real en la
versión horaria.

**3. NTC Marruecos**: no se aplicó ninguna limpieza. Se documentó el problema con números exactos
en `docs/columnas_pendientes_equipo.md` (punto 7) para que el equipo confirme primero el origen y
la fiabilidad del dato — es la única columna del dataset que combina nulos altos (16,8%) y
atípicos altos (9-13%) sin que se haya confirmado todavía si es una particularidad legítima de esa
interconexión (la más pequeña y nueva de las tres) o un problema de la fuente.

**Decisión de prioridad para la siguiente capa de la arquitectura**: se prioriza **BESS
(dimensionamiento y operación de una batería) sobre CNN-LSTM/Transformer**, manteniendo la lógica
ya explicada (el problema identificado es exposición a eventos extremos, no falta de estructura
temporal). **Matiz nuevo para más adelante, no para ahora**: podría tener sentido en el futuro una
señal explícita de "esto se está pareciendo a un régimen atípico" (por ejemplo, una aceleración
inusual del precio del gas) que alimente tanto al modelo puntual como a la capa de incertidumbre —
no una red nueva, una feature más. Queda anotado para cuando se retome esa discusión, no es una
tarea activa todavía.

---

## 25. Corrección de método: una sola semilla no basta para medir un efecto pequeño (y dos resultados nuevos que sí se sostienen)

**Corrección importante, con transparencia**: al repetir la prueba de redundancia del punto 2 de
la nota 24 tras añadir una columna nueva sin relación (`regimen_tope_gas`), el resultado se
invirtió — quitar `entsoe_load` pasó de mejorar el MAE en 0,12 a solo 0,02. La causa: LightGBM
muestrea aleatoriamente qué columnas mira en cada árbol (`colsample_bytree≈0,77`), y aunque la
semilla sea la misma, cambiar el número total de columnas del dataset cambia qué columnas caen en
ese muestreo. Con diferencias tan pequeñas (bajo el 1% del MAE), ese ruido basta para invertir el
signo del resultado.

**La prueba correcta**: repetir cada comparación con varias semillas del modelo (mismos datos,
solo cambia el azar interno de LightGBM) y quedarse solo con los efectos que mantienen el mismo
signo en todas. Se hizo con 5 semillas sobre las mismas 4 parejas ESIOS/ENTSO-E — **ninguna de las
4 mostró un efecto consistente**: el signo cambió entre semillas en los 4 casos. Conclusión final,
que sí es fiable: **las 4 columnas son seguras de quitar del dataset horario sin coste real de
precisión** — confirma la hipótesis original de la nota 24, pero demostrada correctamente esta
vez. La afirmación anterior (que `entsoe_load_forecast_mw` "sí aportaba valor real") queda
retirada — era ruido de una sola corrida, no un hallazgo real.

**Lección de método para el resto del proyecto, incluido lo ya hecho**: cualquier diferencia de
MAE por debajo de aproximadamente el 1-2% no debe reportarse a partir de una sola corrida —
conviene repetir con varias semillas antes de anotarla como una mejora o un empeoramiento real,
tal como ya se hace con el error estándar de muestra del compañero de equipo (nota 23).

**Dos resultados que sí se sostienen, con evidencia más sólida** (medidos con un solo entrenamiento
pero con efectos grandes, muy por encima del ruido visto arriba):

- **`regimen_tope_gas`** (indicador del mecanismo ibérico de tope al gas, 15-jun-2022 a
  31-dic-2023, verificado de forma independiente) mejora la calibración de la capa de
  incertidumbre: cobertura global 70,3% → 74,4%, y **cobertura en evento extremo 54,2% → 63,2%**
  (+9 puntos), a costa de un intervalo solo 1,6 €/MWh más ancho. El evento extremo medido es el
  apagón de 2025 — la mejora no viene de que la variable esté "activa" en esas horas (el tope al
  gas ya no estaba vigente en 2025), sino de que el modelo aprendió mejor las relaciones de precio
  durante el entrenamiento al poder separar el régimen del tope de los años normales. **Se
  recomienda adoptarla como feature por defecto.**
- **Métricas económicas** sobre el LightGBM horario ganador (validation 2025): captura de
  arbitraje 91,0% (persistencia: 81,4%), acierto de hora pico ±1h 79,4% (persistencia: 67,6%). Con
  la salvedad ya conocida de que esto es sobre validation, no sobre el test donde se miden los
  resultados del compañero — referencia interna sólida mientras se decide cuándo comparar en test.

---

## 26. Primer simulador de BESS (batería de red) — traduciendo el % de arbitraje a euros

Arranca el capítulo de baterías. Las métricas de la nota 25 dicen qué porcentaje del arbitraje
teórico captura el modelo, pero no cuántos euros representa eso para una batería real — este
simulador (`modelos/simulador_bess_horario.py`) da ese paso.

**Batería de referencia usada (versión 1, ajustable)**: como todavía no hay un tamaño de proyecto
definido por el equipo, se usan tres duraciones típicas de las licitaciones de baterías de red en
España (1h, 2h y 4h de autonomía), sobre 1 MW de potencia (el resultado se reporta "por MW
instalado", así que escalar a un proyecto real de tamaño concreto es multiplicar), con 90% de
eficiencia ida y vuelta (típico de baterías de ion-litio). Estrategia simple: un ciclo de
carga/descarga al día — carga en las horas más baratas predichas, descarga en las más caras
predichas, un MWh de energía por cada hora de autonomía.

**Resultado** (ingreso anualizado, validation 2025, tres estrategias — decidir con el modelo,
decidir con persistencia, y el límite teórico de decidir con el precio real que nunca se conoce de
antemano):

| Duración | Con el modelo | Con persistencia | Límite teórico (oráculo) | % del límite capturado (modelo) |
|---|---|---|---|---|
| 1h | 28.807 €/año | 25.961 €/año | 31.433 €/año | **91,6%** |
| 2h | 55.757 €/año | 50.537 €/año | 59.383 €/año | **93,9%** |
| 4h | 100.809 €/año | 92.035 €/año | 105.601 €/año | **95,5%** |

**Lectura para la memoria**: el modelo captura de forma consistente 91-96% del valor económico
máximo posible, y le saca entre 8 y 10 puntos porcentuales de ventaja a la persistencia en las
tres duraciones — una diferencia de varios miles de euros al año por MW instalado. La ventaja
relativa del modelo crece ligeramente con la duración (91,6% → 95,5%), señal de que el modelo
acierta mejor identificando el *conjunto* de horas buenas/malas del día que el momento exacto de
una sola hora.

**Limitaciones explícitas de esta primera versión, para no sobrevender el resultado**: un solo
ciclo por día (no ciclado múltiple), sin degradación de la batería, sin restricciones de red ni
costes de operación/mantenimiento, y sin un tamaño de proyecto real todavía confirmado por el
equipo — es una herramienta para comparar estrategias (modelo vs. persistencia vs. límite teórico),
no para dimensionar una inversión real.

---

## 27. La captura de arbitraje no es una sola métrica — encontrado al comparar con el resto del equipo

Una compañera detectó, revisando el trabajo de todos, algo que ya estaba pasando dentro de nuestro
propio proyecto sin que lo hubiéramos notado: el mismo baseline (`persistencia_d1`) tiene una
"captura de arbitraje" de 86,5% en `notebooks/F11_baselines.ipynb` y de 81% en nuestra propia
radiografía del modelo. Se investigó la causa exacta comparando el código de ambos, en vez de
suponerla.

**La causa, verificada**: "captura de arbitraje" no es una fórmula única, son varias decisiones de
diseño distintas que cada notebook tomó por su cuenta:

| | `F11_baselines.ipynb` | Capítulo 6 de la radiografía (`metricas_economicas_horario.py`) | Capítulo 7 de la radiografía (`simulador_bess_horario.py`) |
|---|---|---|---|
| Horas de carga/descarga por día | 4 (batería de 4h fija) | 1 (una sola hora) | 1, 2 o 4 (varias duraciones) |
| Eficiencia ida-vuelta | 85% | sin modelar (0% de pérdida) | 90% |

**Hallazgo incómodo pero importante: la inconsistencia no era solo entre notebooks de distintas
personas, estaba también DENTRO de nuestro propio informe** — el capítulo 6 (métrica de una sola
hora, sin pérdidas) y el capítulo 7 (el simulador BESS de verdad, con eficiencia) miden cosas
distintas y no son comparables entre sí, aunque los dos aparezcan en el mismo documento. Queda
documentado aquí con la misma honestidad que el resto del proyecto: fue un descuido, no una
decisión.

**La solución que propone el equipo, y con la que estamos de acuerdo**: una sola función de
arbitraje compartida, dentro de `evaluar_modelos.py`, con los supuestos que ya definimos en
`simulador_bess_horario.py` (1 MW, 90% de eficiencia, un ciclo al día) como estándar del equipo —
la propuesta del equipo fija además una sola duración de referencia para el ranking (2h) y deja 1h
y 4h como análisis de sensibilidad. En cuanto esa función exista, el capítulo 6 de nuestra propia
radiografía debería recalcularse con ella y dejar de usar su fórmula de una sola hora.

## 28. Entregable para el leaderboard del equipo — `pred_val_2025.csv` + `metadata.json`

Por el calendario que propuso el equipo (modelos cerrados el domingo 30-ago, un evaluador común
calcula todas las métricas para los doce a la vez), se generó el entregable pedido en
`modelos/lgbm_horario_afinado_cqr/` — sin calcular nuestro propio MAE ni captura de arbitraje para
reportar, tal como pide el plan del equipo.

**Al construirlo, dos hallazgos que valía la pena registrar:**

1. **Los artefactos guardados habían quedado desactualizados**: la retirada de las 3 columnas
   duplicadas de ENTSO-E (nota 24) se aplicó al script del dataset pero nunca se reentrenó el
   modelo final con el dataset ya limpio. Se reentrenaron los tres modelos (puntual, incertidumbre,
   calibración conforme) sobre el dataset actual antes de generar el entregable. El MAE se movió de
   12,55 a 12,63 €/MWh — un cambio pequeño (0,08, dentro del ruido de semilla ya documentado en la
   nota 25), no una regresión real.
2. **Falta exactamente 1 hora de las 8.760 esperadas de 2025**: `2025-10-26 00:00 UTC`, justo el
   cambio de hora de octubre. Se verificó directamente en `spot_price`: esa hora no tiene ningún
   valor, ni siquiera nulo — un hueco real de la tabla fuente, no un error de nuestro código (que
   ya trabaja en UTC desde el principio, precisamente para evitar este tipo de problema). Se
   entregaron 8.759 filas con la ausencia documentada, en vez de inventar un valor.

---

## 29. Fuga real encontrada y corregida: las columnas de commodities usaban un día demasiado reciente

Un compañero de equipo hizo una auditoría específica de "frontera de fuga" (qué dato existe
realmente a las 12:00 del día de decisión, que es cuando cierra la subasta del día objetivo) y
encontró algo que nos afecta directamente: el gas y el CO2 (fuente Trayport) cierran su sesión de
mercado a las ~17:30 — **después** del cierre eléctrico de las 12:00.

**Lo que hacíamos mal**: para cada fila (una hora del día objetivo X), usábamos el valor de
commodities fechado en X-1. Pero a las 12:00 de X-1 (el momento real en que se toma la decisión),
la sesión de commodities de X-1 todavía no ha cerrado — cierra esa misma tarde, a las 17:30. La
última sesión ya cerrada y disponible en ese momento es la de X-2, no la de X-1. Estábamos usando
un dato un día más reciente de lo que existía en el momento real de la predicción.

**Verificación antes de corregir, no solo confianza en la auditoría ajena**: se probó el coste de
usar la versión segura (X-2 en vez de X-1) reentrenando el modelo — el MAE subió de 12,63 a
12,74 en una prueba aislada (+0,11, dentro del ruido de semilla ya documentado en la nota 25). Al
aplicarlo de forma definitiva y reentrenar los tres modelos (puntual, incertidumbre, calibración),
el MAE quedó en 12,86 — un cambio pequeño, no una caída del rendimiento real del modelo, solo la
corrección de una ventaja injusta que no debía estar ahí.

**Ya corregido** en `construir_dataset_horario.py` y reentrenados los tres artefactos de
producción (puntual, incertidumbre, calibración conforme) y el entregable del leaderboard del
equipo (`modelos/lgbm_horario_afinado_cqr/`). Pendiente: aplicar el mismo criterio en
`construir_dataset_maestro.py` si el dataset diario se retoma alguna vez (hoy no se usa para
modelar, nota 24).

**Lección para la memoria**: este tipo de fuga es más difícil de detectar que las evidentes (usar
el precio real del propio día objetivo) porque el dato "ya existe" en la base de datos con una
fecha que *parece* razonable — el error está en la hora exacta de publicación de la fuente
original, no en la estructura del dataset. Vale la pena que quede documentado como ejemplo
concreto de por qué la frontera de fuga hay que verificarla contra la hora de cierre real de cada
fuente, no solo contra su fecha.

---

## 30. El equipo converge en una matriz única ("núcleo") — y ya incorpora nuestras correcciones

Un compañero de equipo construyó una herramienta de auditoría de fuga (`scripts/auditoria_frontera.py`)
que mide, contra la tabla fuente real, qué día describe cada columna — sin fiarse de los nombres.
Aplicada sobre `data/gold/matriz_nucleo.parquet` (la matriz que un análisis multi-modelo con
varias semillas identificó como la más consistente entre cuatro candidatas), el veredicto es
limpio: **"ninguna columna describe el día objetivo. Frontera respetada."**

Dos hallazgos notables al revisarla:
- **Confirma de forma independiente nuestra corrección de la nota 29**: la auditoría mide que
  commodities describe D-2 respecto al día objetivo (con un margen de 54,7 puntos sobre la
  siguiente opción) — exactamente la corrección que acabábamos de aplicar por nuestra cuenta.
- **Ya incluye dos features que propusimos nosotros**: `d1_es_puente` y `d1_regimen_tope_gas`
  están en su catálogo de columnas.

**Decisión**: adoptar `nucleo` como la matriz compartida del proyecto, en vez de mantener
`construir_dataset_horario.py` como una construcción paralela. Se reentrenó nuestro LightGBM
sobre ella (`modelos/modelo_lightgbm_nucleo.py`), reutilizando los hiperparámetros ganadores de
Optuna (sin re-afinar todavía, por tiempo — sería el siguiente paso si el modelo sigue siendo
candidato serio):

| | MAE (validation) |
|---|---|
| LightGBM sobre `nucleo` | **12,92 €/MWh** |
| LightGBM sobre nuestra propia matriz horaria (nota 29) | 12,86 €/MWh |

Prácticamente el mismo resultado (diferencia de 0,06, muy dentro del ruido de semilla ya
documentado) — confirma que ambas matrices capturan una señal equivalente, y respalda converger
en una sola sin perder nada.

**Pendiente de aclarar con el equipo, no resuelto aquí**: la tabla comparativa que circuló junto
con el análisis de matrices reporta "211 días de test" — coincide con el conjunto 2026 que el
plan de productivización del equipo acordó abrir una sola vez, con los modelos ya congelados, el
31 de agosto. Si esa tabla ya comparó y ordenó varias variantes de modelo usando esos datos, es el
riesgo de "quemar el test" que el propio plan advertía por escrito. Queda como pregunta abierta
para el equipo, no una decisión que se tome unilateralmente aquí.

---

## 31. Primera versión mínima de Transformer — confirma el patrón exacto que ya habíamos visto

**Objetivo, fijado antes de ver el resultado (no reinterpretado después)**: no se esperaba superar
a LightGBM en precisión — la evidencia acumulada (notas 14, 18, 19, 22, 23) apunta en contra de esa
expectativa, y con ~1.800 días de entrenamiento este es un dataset modesto para un Transformer.
Esto no es solo intuición del equipo: está documentado en la literatura de forecasting. Zeng,
Chen, Zhang y Xu, *"Are Transformers Effective for Time Series Forecasting?"* (AAAI 2023,
[arXiv:2205.13504](https://arxiv.org/abs/2205.13504)), muestran que modelos lineales de una sola
capa (LTSF-Linear) igualan o superan a varios Transformers de forecasting de largo plazo, y
argumentan que el mecanismo de auto-atención, al ser invariante a la permutación del orden de
entrada, pierde precisamente la información temporal que el problema necesita — un mecanismo
distinto pero compatible con lo que ya veníamos observando en este proyecto (que la ventaja de
LightGBM no viene de falta de "atención", sino de que los lags manuales ya codifican el orden
temporal de forma explícita). Vale la pena citarlo tal cual en la memoria al llegar al capítulo de
Transformers, como respaldo académico del objetivo que se fijó de antemano.

El objetivo real era doble: (1) comprobar si un modelo que ve la secuencia cruda de 168 horas de
precio real (sin la ingeniería manual de lags) encuentra algo que nuestras features no capturan, y
(2) ver si predecir las 24 horas del día a la vez produce una forma más coherente, útil para el
valor económico aunque no para el MAE puro — el mismo patrón que ya mostró el Seq2Seq del
compañero (nota 23).

**Arquitectura**: encoder-decoder pequeño con atención (46.785 parámetros, `d_model=32`, 2 capas).
El encoder ve las 168 horas reales de precio de la semana anterior al día objetivo (sin fuga); el
decoder, para cada una de las 24 horas, consume las mismas 122 features "seguras" que usa
LightGBM (previsiones, calendario, NTC, capacidad) y hace atención cruzada sobre el encoder.
Entrenado sobre la matriz `nucleo`, 33 épocas hasta la parada temprana.

**Resultado — se cumplió la predicción hecha de antemano, con números concretos**:

| | LightGBM (nucleo) | Transformer mínimo |
|---|---|---|
| MAE | **12,92 €/MWh** | 14,14 €/MWh |
| Captura de arbitraje | 91,0% | **93,3%** |
| Acierto hora pico ±1h | 79,4% | **83,0%** |

Exactamente el patrón anticipado y el mismo que encontró el compañero con su Seq2Seq: peor
precisión puntual, mejor valor económico. No es una casualidad aislada — es la segunda vez que
aparece este mismo patrón en el proyecto, con dos arquitecturas secuenciales distintas construidas
por dos personas distintas. Eso lo hace un hallazgo más sólido para la memoria que si solo
hubiera pasado una vez.

**Lectura para la memoria**: el Transformer no reemplaza a LightGBM como modelo puntual, pero es
un candidato serio para la capa de decisión de la batería (capítulo BESS) precisamente porque
optimiza mejor el *orden relativo* de las horas del día, que es lo que de verdad importa para
cargar y descargar — no el precio exacto. Queda documentado como comparación honesta, no como
"ganador" ni "descartado".

---

## 32. El "nuevo récord" de 11,95 no se adopta todavía — el propio autor avisó de una fuga sin corregir

Un compañero reportó un ensemble sobre `nucleo + meteo ECMWF` con MAE 11,95 (mejor que cualquier
resultado del proyecto hasta ahora) y, en el mismo mensaje, algo que hay que tomarse muy en serio:
*"al volver a revisar las matrices estaba metiendo valores del día D (día de predicción) y en el
momento de la predicción esos valores no son conocidos, así que toca tocar un poco los valores
para que funcione con el mismo tipo de parámetros que en producción."*

Es, en sus propias palabras, una fuga de información sin corregir todavía (verbo en futuro:
"toca tocar" — no "ya corregí"). No se puede verificar por nuestra cuenta con la herramienta de
auditoría (`scripts/auditoria_frontera.py`) porque esa variante concreta (con el canal ECMWF
añadido) no está todavía en nuestro repositorio local.

**Decisión, por prudencia**: **no se adopta el 11,95 como resultado del proyecto ni se usa en la
memoria hasta que se confirme la corrección y se vuelva a correr.** El propio `nucleo` (sin ese
canal, MAE 12,32 sobre el mismo test) sigue siendo el número verificado y auditado — ese es el que
usamos si hoy hay que citar algo.

**Complicación real que esto introduce**: el usuario confirmó que los 211 días de test de la
tabla comparativa **ya cuentan como la apertura oficial del test compartido** que el plan de
productivización reservaba para el 31 de agosto, con los modelos ya congelados. Si esa misma tabla
incluye una entrada construida con una fuga todavía sin corregir, la apertura oficial del test se
hizo sobre un conjunto de resultados parcialmente contaminado. No es motivo de alarma — es
exactamente el tipo de cosa que conviene decir en voz alta cuanto antes, no descubrir en la
defensa. Vale la pena plantear en el grupo, con la misma calma con la que se han resuelto las
demás: una vez corregida la fuga de `nucleo + meteo ECMWF`, ¿se re-abre el test para esa variante
en concreto, o se da por buena la apertura ya hecha y esa variante queda fuera de la comparación
final por haber llegado tarde y con un defecto conocido?

**Dato adicional útil para esa conversación**: la matriz `moderna` (ventana 2023-01-01 en
adelante, 1.308 días, excluye por completo la crisis de 2021-2022) rindió peor que `nucleo`
(13,76 vs 12,32 de MAE) en la comparación multi-modelo. Coincide con nuestro propio hallazgo de la
nota 22 (un modelo sin exposición a la crisis pierde contra la persistencia) — es una segunda
confirmación, con datos de otra fuente, de que recortar la ventana de entrenamiento para "evitar"
la crisis sale caro, no barato.

---

## 33. Cierre formal del Transformer, y primer paso del asistente (LLM + herramientas, no RAG documental)

**Transformer**: se documentó en `notebooks/06_justificacion_no_transformer.ipynb` (con datos
reales, no ilustrativos) la decisión de no seguir invirtiendo tiempo en F13. Cinco líneas de
evidencia independientes: (1) el equipo ya comparó 7 arquitecturas de deep learning con 3
semillas cada una sobre la misma matriz, sin que el Transformer explore territorio nuevo; (2) el
ruido de inicialización del propio equipo (hasta ~1,2 €/MWh entre semillas del mismo modelo) hace
que cualquier mejora futura del Transformer necesite validación multi-semilla para ser creíble;
(3) el objetivo del proyecto es captura de arbitraje, no MAE, y ahí tampoco gana con claridad; (4)
nuestra propia curva de entrenamiento se estanca por encima de LightGBM sin señales de que más
épocas lo resuelvan; (5) la literatura (Zeng et al., AAAI 2023) ya anticipaba este resultado antes
de correr un solo experimento. Sumado al calendario (F13 declarado prescindible en tres informes
sucesivos del equipo, cierre total el 9 de septiembre), la decisión es no retomarlo salvo que
sobre tiempo el 11 de septiembre.

**Asistente (LLM + herramientas)**: se aclaró con el usuario que lo que se necesita no es RAG
documental clásico (recuperar y citar texto) sino un patrón de **"tool use" / function calling**:
el modelo de lenguaje entiende la pregunta y llama a funciones deterministas que hacen el cálculo
real — los números nunca salen del LLM. Distinción central del diseño, para que el sistema sea
defendible ante el tribunal: **"predicción"** solo existe para D+1 (el único horizonte real del
proyecto, sale del modelo entrenado) — cualquier otro horizonte (una semana, un mes, un rango de
años) se responde como **"referencia histórica"** (percentiles del precio real ya ocurrido en
circunstancias parecidas), nunca disfrazado de predicción del modelo.

Primera pieza construida y probada: `modelos/asistente/herramientas.py` — tres funciones
deterministas (`precio_historico_percentiles`, `precio_historico_serie`, `simular_bateria` con
parámetros de batería a elección, y `prediccion_d_mas_1`).

**Hallazgo real al probar `prediccion_d_mas_1`, no ocultado**: el dataset usa `DATASET_END`, una
constante fija (hoy "2026-08-15") que el equipo congeló a propósito para que la comparación de
matrices del 30-31 de agosto sea reproducible. Como consecuencia, la función daba la predicción
del día siguiente a esa fecha congelada, no de mañana en sentido literal — exactamente la pieza
que el propio equipo ya tiene identificada como pendiente ("features de D+1 desde Postgres", P1
en las tareas del 29-ago). No se tocó `DATASET_END` (rompería la comparación de todo el equipo el
día que se elige el modelo principal) — en su lugar, la función ahora compara la fecha objetivo
contra "mañana" real y devuelve una `advertencia` explícita cuando no coinciden, en vez de fingir
que da una predicción actual.

**Primera conversación real con el asistente ya conectado** (`modelos/asistente/chat.py`, patrón
"tool use" del SDK oficial de Anthropic — `@beta_tool` + `tool_runner`, modelo `claude-opus-5`):
se le preguntó *"¿Cuánto ha costado la luz históricamente los domingos de agosto entre las 20h y
las 21h?"* y respondió correctamente citando los percentiles reales de ambas horas, etiquetando
todo como *"Referencia histórica (no es una predicción del modelo)"* sin que se le insistiera, y
añadió por su cuenta un matiz honesto sobre el tamaño de muestra (32 horas, sin acotar por año) —
exactamente el comportamiento que el system prompt pedía. Confirma que el diseño (herramientas
deterministas + regla explícita de "predicción vs. referencia histórica" en el prompt) funciona
en la práctica, no solo en el papel.

**Nueva herramienta**: `precio_negativos(anio)` — el precio spot español sí puede ser negativo
(exceso de renovables sin demanda que lo absorba, no es un error de datos). Dato real encontrado
al probarla: en 2026, **681 de 5.831 horas (11,68%)** tuvieron precio negativo, con un mínimo de
**-9,83 €/MWh el 29 de marzo a las 14:00** — frente a **0 horas negativas en 2023**. Confirma con
un número concreto la tendencia, ya intuida en el EDA del equipo, de que las horas de precio
negativo se han vuelto mucho más frecuentes con el crecimiento de la solar.

**RAG documental construido**: `modelos/asistente/indexar_documentacion.py` trocea
`notas_memoria_tfm.md` y `columnas_pendientes_equipo.md` por nota numerada (42 chunks en total,
cada nota ya es una unidad semántica coherente, no hizo falta partir por tamaño de texto), genera
embeddings con un modelo local y multilingüe (`fastembed`,
`paraphrase-multilingual-MiniLM-L12-v2`, 384 dimensiones — sin necesidad de una segunda clave de
API de pago, ya que Anthropic no ofrece embeddings propios) y los guarda en Postgres con
`pgvector`. Probado con *"¿por qué no se usa un Transformer en el proyecto?"*: recuperó
exactamente las notas 31 y 33 (las correctas) como las más similares, y el asistente completo
sintetizó una respuesta correcta citando las fuentes, sin inventar nada. Con esto el asistente ya
cubre las dos mitades del diseño original: herramientas deterministas para datos/predicción, y
RAG semántico para metodología/decisiones.

**Hallazgo colateral, mismo patrón que la nota 28**: al construir el simulador de autoconsumo
solar apareció otro hueco de 1 hora en `spot_price`, esta vez en el cambio de hora de **octubre
de 2024** (`2024-10-27 00:00 UTC`) — el mismo patrón exacto que ya se había documentado para 2025.
Confirma que es un hueco recurrente cada año en la transición de octubre, no un incidente aislado.
Se hizo tolerante con `interpolate(limit=1)` en vez de fallar la simulación por un hueco de una
sola hora, con una comprobación explícita de que no se cuele un hueco más grande sin avisar.

## 34. Simulador de autoconsumo solar + batería — primera versión, para mostrar avance al equipo

Extiende `simular_bateria` con generación solar real (ERA5, `ssrd_mean`) y un perfil de consumo,
simulando hora a hora: la solar cubre primero el consumo directo, el excedente carga la batería,
el déficit lo cubre la batería o -si no alcanza- el mercado. Probado con un caso realista (300 kWp
solar + batería 0,5 MW/1 MWh, empresa de 500 MWh/año, backtest 2024): **26.138 € de ahorro anual,
77,9% de autoconsumo**, sobre un coste de referencia de 31.429 € comprando todo al mercado.

**Documentado como versión 1, con limitaciones explícitas que el asistente siempre repite en la
respuesta** (no las omite ni las resume): perfil de consumo plano (no la curva real del cliente),
generación solar simplificada (sin pérdidas por temperatura/orientación/inversor), sin
degradación de batería ni costes de operación.

**Siguiente mejora concreta, ya con un diseño claro**: sustituir el perfil plano por la curva de
consumo real que aporte el cliente, y sobre esa curva aplicar la misma idea que la capa de
incertidumbre de LightGBM (notas 20-21) — no predecir el precio, sino **extrapolar el consumo
propio del cliente** a uno o dos años con rangos (p10/p50/p90) en vez de un único número, usando
su propio histórico como base. Es la aplicación correcta de "extrapolar con percentiles" que se
había planteado antes para baterías a 20 años (nota 33) — aquí sí encaja, porque parte de datos
reales que el cliente aporta, no de una serie que no existe.

**Construida y probada**: `extrapolar_consumo_cliente(historico_mensual_mwh, anios_a_futuro)`.
Casualmente, el mismo día un compañero de equipo construyó `scripts/curva_precios.py` con
exactamente la misma filosofía para el precio a 20 años (nivel × estacionalidad + residuo →
percentiles, con el nivel explícitamente "NO SE PREDICE, SE APORTA" desde fuera) — confirma de
forma independiente que el enfoque era el correcto. Se reutilizó la misma lógica para consumo:
nivel = el más reciente del cliente (sin asumir tendencia de crecimiento), estacionalidad = la del
propio histórico, y la banda p10/p90 sale de la variabilidad real mes a mes, no de un supuesto.
Probado con un caso sintético (24 meses, pico de climatización en verano): proyectó correctamente
el patrón estacional con bandas estrechas (esperable con solo 2 años de histórico, tal como avisa
la propia herramienta en sus `limitaciones`).

---

## 35. Fuga meteorológica corregida por el equipo — verificado que NO afecta a `nucleo` ni a nuestro modelo

El equipo corrigió una fuga real: los lags meteorológicos (`_met_Dm1`, `_met_Dm2`) salían de ERA5
(reanálisis, publicado con 5 días de retraso) incluso en fechas donde ya existía previsión ECMWF
real — a las 11:00 de D esos valores de ERA5 todavía no existían. La corrección hace que, desde
que hay ECMWF disponible (abril de 2024 en adelante), los lags usen ECMWF.

**Antes de asumir que había que reentrenar, se verificó directamente**: se comparó celda a celda
las 8 columnas meteorológicas de lag entre `matriz_nucleo` (la que usa nuestro LightGBM) y la
nueva `matriz_produccion` (donde sí se aplicó la corrección), sobre las 57.521 filas que ambas
comparten — **cero diferencias**. La corrección no cambió ningún valor en el período que ya
teníamos entrenado; su efecto real está en la parte más nueva de los datos, ya incorporada en
`matriz_produccion` pero no en `matriz_nucleo`. Conclusión: nuestro modelo actual sigue siendo
válido, no hace falta reentrenar por este motivo.

**También llegó al repo**: `production/api/` (panel FastAPI + tabla `predictions` en Postgres,
para mostrar predicciones de varios modelos en un mismo gráfico) y `scripts/curva_precios.py`
(la curva de precio a 20 años con la metodología de percentiles ya descrita en la nota 34).

## 36. El asistente entra en el panel del equipo — sin marca de Claude visible

En vez de una página aparte, el asistente se integró directamente en `production/api` (el panel
del equipo que llegó en la nota 35) — así la página sigue siendo del grupo, sin ningún indicio de
qué modelo hay detrás:

- **Backend**: un endpoint nuevo, `POST /api/asistente` (`production/api/main.py`), que reenvía
  la pregunta a `modelos/asistente/chat.py` y devuelve la respuesta. Los dos endpoints que ya
  existían (`/api/rango`, `/api/dia/{dia}`) no se tocaron.
- **Frontend**: una sección "Asistente del proyecto" añadida a `production/api/static/index.html`,
  con el mismo estilo visual oscuro que el resto del panel (mismos colores, misma tipografía) —
  caja de pregunta, tres sugerencias de ejemplo, y el área de respuesta.
- **Probado de punta a punta**: servidor local (`uvicorn production.api.main:app --port 8000`),
  pregunta real vía `POST /api/asistente` → respuesta correcta con los datos reales (681 horas
  negativas, -9,83 €/MWh). La página y `/api/docs` se siguen sirviendo con normalidad.

**Nota de seguridad, ya decidida en la nota 33 y que sigue aplicando aquí**: cada persona necesita
su propia `anthropic_api_key` en su `credentials.json` local para que el endpoint funcione en su
máquina — no se sube ninguna clave al servidor compartido todavía. Si el equipo decide llevar esto
al VPS de producción, hay que decidir antes cómo se gestiona esa clave (ver la propia nota 33).

## 37. Dos cierres antes de enseñar el asistente al equipo: alcance cerrado y gráficas en la respuesta

**Alcance cerrado, verificado con una pregunta real**: se probó *"¿cuál es la capital de Francia?"*
y el asistente respondió "París" antes de redirigir — es decir, el modelo de lenguaje sí puede
contestar cultura general con su propio conocimiento de entrenamiento (no busca en internet, pero
tampoco se queda en blanco). Para una pregunta de datos fuera del alcance (*"¿precio del petróleo
Brent?"*) sí se comportó bien: se negó a inventar una cifra y explicó por qué. Se cerró la primera
grieta añadiendo una regla explícita al system prompt — verificado de nuevo, ahora la pregunta de
Francia también se rechaza. Para explicarlo con precisión si alguien del equipo pregunta: el
asistente **nunca fabrica datos/números** (eso ya estaba garantizado por diseño, herramientas
deterministas), pero sin esta regla sí podía "charlar" de temas ajenos al proyecto usando
conocimiento general del modelo — ya cerrado.

**Gráficas en la respuesta**: se añadió la herramienta server-side `code_execution` (matplotlib
preinstalado en el sandbox de Anthropic) a las herramientas disponibles. El asistente, cuando la
pregunta pide ver una curva, primero llama a la herramienta de datos correspondiente (nunca
inventa números para graficar) y con esos números reales genera la figura. Probado con *"muéstrame
una gráfica de cómo varía el precio histórico según la hora del día en 2025"*: generó la imagen y
un análisis correcto (valle solar 10h-16h con p25 prácticamente en 0 €/MWh, pico de noche
19h-22h). La imagen se recupera vía `client.beta.files.download()`, se codifica en base64, y tanto
`production/api/main.py` como el widget de `index.html` ya la muestran — probado de punta a punta
por la API real, no solo en terminal.

**Decisión de alcance para `preguntar()`**: se mantuvo la función original devolviendo solo texto
(para no romper el uso ya existente en terminal) y se añadió `preguntar_con_imagenes()` aparte,
que es la que usa el endpoint de la API.

## 38. La fuente del RAG documental no puede ser solo mis notas — se amplió con el código del equipo

Al mostrar el artifact del asistente, surgió la pregunta correcta: *"¿la documentación que usa el
asistente nace de mis propias notas?"*. Respuesta honesta: sí, hasta ahora el corpus del RAG eran
únicamente `notas_memoria_tfm.md` y `columnas_pendientes_equipo.md` — verificados contra la base de
datos punto por punto, pero escritos por una sola persona y no revisados por el resto del equipo.
No es una fuente "oficial", y es exactamente lo que preguntaba también un compañero: *"¿con qué se
está alimentando esta IA?"*.

**Mejora concreta ya aplicada**: se añadió al índice el docstring de cabecera de cada script del
equipo (`scripts/*.py`, `production/api/main.py` — 25 archivos con documentación técnica real en su
propio código, escrita por varias personas distintas, no solo yo). Es una fuente más objetiva
porque está verificada por el hecho de que el código corre y hace lo que el docstring dice — no es
interpretación de una sola persona. El corpus pasó de 42 a 71 fragmentos (`buscar_documentacion`
ya devuelve resultados mezclando notas propias y docstrings de compañeros — probado con una
pregunta sobre `matriz_nucleo`, que trajo la nota 30 junto con los docstrings de
`construir_matriz_produccion.py`, `rejilla_matrices.py`, `depurar_matriz.py` y
`preparar_tensores.py`).

**Lo que esto NO resuelve**: sigue sin ser un documento aprobado explícitamente por el equipo en
una reunión — solo es más objetivo y multi-autor que antes. La pregunta de fondo ("¿qué fuente de
información sobre el proyecto consideramos oficial?") queda pendiente de discutir en vivo con el
equipo, no es algo que se pueda resolver unilateralmente aquí.

**Sobre la extrapolación de consumo a 5-10 años (pregunta de un compañero)**: `extrapolar_consumo_
cliente()` **no usa ninguno de los modelos de predicción de precio cargados en el servidor**. Es
completamente independiente del LightGBM/ensemble: toma solo el histórico de consumo mensual que
aporte el propio cliente y aplica una descomposición estadística (nivel + estacionalidad +
percentiles del residuo), la misma familia de método que `scripts/curva_precios.py` usa para la
curva de precios a 20 años — deliberadamente NO encadena el modelo D+1 hacia el futuro (el propio
script de precios documenta por qué: el error se acumula año a año). Caveat importante para la
reunión con el equipo: lo probado y validado hasta ahora son horizontes de 1-2 años; extender a
5-10 años debilita bastante los dos supuestos del método (nivel del último año se mantiene
constante, y la estacionalidad se calcula sobre el propio histórico del cliente) — con pocos años
de historial de partida, cuanto más lejos se proyecta, menos fiables son las bandas p10/p90.

## 39. Pendiente a futuro (no es nuestro trabajo ahora): parámetros de batería en la interfaz + €/MWh capturado

Un compañero, sin ser parte del trabajo de este bloque (la interfaz web no la construimos
nosotros), adelantó una idea de diseño para cuando se aborde: dejar espacio en los laterales de
la página para que el usuario introduzca las características reales de su batería, y que la
simulación diaria devuelva, además del ahorro en euros, un valor de **€/MWh capturado** — el
precio medio efectivo que la operación de la batería logró aprovechar (diferencia entre el precio
al que vendió/descargó y al que compró/cargó), en vez de solo el ahorro total. Es una métrica más
comparable entre baterías de distinto tamaño.

Los parámetros de batería que propuso, para tenerlos en cuenta si esto avanza:
- Número de horas de autonomía de la batería.
- Potencia (MW) — con la duda, sin resolver todavía, de si el dato correcto a pedir es potencia o
  MWh (capacidad); habría que revisarlo cuando se diseñe el formulario.
- Degradación cada 1.000 ciclos.
- Ciclos máximos de vida.
- Porcentaje de carga máxima y mínima (los límites de SoC — no cargar al 100% ni descargar al 0%
  alarga la vida útil real).

Ninguno de estos parámetros está hoy en `simular_bateria` ni en `simular_autoconsumo_solar`
(ambas asumen eficiencia de ida/vuelta fija y ningún límite de degradación o de SoC) — queda
anotado aquí para cuando se decida ampliar el simulador, no como algo que haya que construir ya.

## 40. Prueba en vivo con el equipo: dos huecos corregidos en el asistente, y un hallazgo importante

Prueba en vivo con un compañero, con dos consecuencias directas y un hallazgo pendiente de acordar
en equipo.

**Hueco 1, corregido — "lista las horas con los precios más negativos de 2026"**: el asistente
respondió que no podía mostrar esto, y era cierto para las herramientas que existían: `precio_
negativos` solo daba el conteo total y el mínimo absoluto del año, no el detalle. El dato SÍ
estaba en la base de datos. Se añadió `precio_horas_negativas(año, límite)`, que devuelve la
lista día a día (hasta 500 horas) ordenada de más negativa a menos, para tabular o graficar.
Verificado con la misma pregunta: ahora responde con la tabla correcta (las 10 más negativas de
2026 se concentran en domingos de finales de marzo/principios de abril, 12h-16h — canibalización
solar).

**Hallazgo importante — `scripts/curva_precios.py` / `notebooks/07_curva_precios.ipynb`**: un
compañero construyó, de forma independiente, la metodología correcta para "precio a 2027-2046"
que a nuestro asistente le faltaba. Hasta ahora, para cualquier pregunta de precio a largo plazo
el asistente solo tenía `precio_historico_percentiles` (patrones YA ocurridos) — no una curva de
escenario a futuro. `curva_precios.py` sí lo es, y está **validado por backtest** (notebook,
sección 8b: simulando 2025 "a ciegas" con datos hasta 2024, la cobertura P1-P99 sale ~98% y
P10-P90 ~80%, justo lo esperado). La descomposición es `precio(día,hora) = nivel(año) × factor
estacional + forma intradiaria + residuo remuestreado de días reales completos`, con dos
decisiones que vale la pena que quede en la memoria:
- El **nivel** (precio medio del año) NO se predice — lo aporta el equipo (futuros MIBEL o un
  escenario), porque encadenar el modelo D+1 hacia 20 años acumula error hasta aplanarse en la
  media.
- La **forma intradiaria se toma de los ÚLTIMOS 2 años**, no de todo el histórico, porque se está
  deformando: el valle de mediodía pasó de -0,29 €/MWh (2020) a -49,08 (2026) por canibalización
  solar — promediar con 2020 daría una forma que ya no existe. Es, según sus propias palabras, "el
  argumento del capítulo de baterías": lo que hace rentable una batería no es que el precio suba,
  es que el spread se abra.

Se añadió `precio_futuro_curva(desde, hasta, nivel_por_año)` envolviendo esta herramienta, y el
system prompt ahora dirige cualquier pregunta de precio a meses/años vista hacia ella en vez de
hacia los percentiles históricos. Probado con un escenario de nivel 2027-2046: el precio medio cae
un 22% pero el spread (p90-p10) se ensancha — exactamente el patrón que motiva el caso de negocio
de baterías, ahora con evidencia del propio equipo en la respuesta del asistente.

**Bug encontrado y corregido en el propio wrapper**: la función `curva()` exige el nivel de TODOS
los años del rango, no solo unas anclas — dar solo `{2027: 66, 2030: 60}` la rompía con un
`ValueError`. El script YA trae el helper `por_anclas()` para interpolar entre anclas (así lo usa
el notebook), pero nuestra primera versión del wrapper no lo llamaba. Corregido: ahora se acepta
dar solo 2-4 años de ancla, como en el uso real del script.

**Pendiente de diseño, no construido todavía — el plan de "rango de vida de la batería"**: en la
misma reunión se esbozó una idea más ambiciosa: que el cliente aporte las condiciones de su
batería y un rango de fechas (pasado o futuro), y el asistente calcule su vida útil combinando la
curva de precio (histórica si existe, o `curva_precios` si es a futuro) con una lógica de
carga/descarga que se adelante al precio — cargar en las horas baratas, descargar en las caras
(esto último ya lo hace `simular_bateria` día a día, pero solo sobre histórico real, nunca sobre
un escenario futuro). Para el cálculo de "vida" hace falta además lo que quedó anotado en la nota
39 (ciclos máximos, degradación cada 1.000 ciclos, %SoC min/max) — nada de eso existe hoy en el
simulador. Es una pieza grande que toca tres cosas a la vez (curva a futuro + optimización de
despacho + degradación), así que antes de construirla conviene acotar el alcance con el equipo en
vez de improvisarlo.

## 41. Actualización de `main`: nuestro modelo ya está integrado, y es el mejor de los siete — con un bug de checkout que lo escondía

Al traer los últimos cambios de `main` (hotfix de SARIMA, manejo de los dos días del cambio de
hora, reselección de representantes tras reentrenar con ECMWF) aparecieron dos scripts nuevos de
despliegue: `scripts/desplegar_modelos.py` (copia a `production/models` el representante de cada
familia de redes, por MAE de validación, vaciando la carpeta antes para que no quede un modelo
viejo con nombre nuevo) y `scripts/modelos_equipo.py` — este último carga los modelos de
compañeros con el mismo contrato que usa el ensemble del equipo (`predecir(T, m)`).

**Buena noticia, verificada, no solo anunciada**: `scripts/modelos_equipo.py` ya carga nuestro
`lgbm_nucleo` (el export nativo que hicimos para poder subirlo sin pasar por Keras) y lo evalúa
junto a los seis modelos de Magdalena. Corriendo `--evaluar` sobre los 365 días de validación,
**nuestro modelo queda primero de los siete**:

| Modelo | Autor | MAE |
|---|---|---|
| **lgbm_nucleo__s0** | **Willy** | **12,917** |
| xgboost__s2 | Magdalena | 13,024 |
| lightgbm__s1 | Magdalena | 13,048 |
| lightgbm__s0 | Magdalena | 13,150 |
| lightgbm__s2 | Magdalena | 13,192 |
| xgboost__s0 | Magdalena | 13,301 |
| xgboost__s1 | Magdalena | 13,343 |
| naive (precio de D) | — | 19,946 |

**Pero al primer intento `--evaluar` no arrancaba** — LightGBM abortaba el proceso entero con
"Model format error, expect a tree here", sin traza útil. Causa: el equipo ya había diagnosticado
y documentado este mismo problema en `.gitattributes` (fechado ayer) — los `.txt` de LightGBM se
guardan en su formato de texto nativo, y con `core.autocrlf=true` (el valor por defecto de git en
Windows) un `checkout` les mete `CRLF`, que el parser de LightGBM no tolera. La regla `-text` ya
cubre `modelos/**/*.txt`, pero **solo protege checkouts nuevos** — los archivos que ya estaban en
disco de antes (nuestro propio `modelo.txt`, y los tres `.txt` de Magdalena) seguían con el `CRLF`
viejo. Corregido localmente quitando los `\r` sueltos y verificado con `git diff`/`git hash-object`
que el contenido coincide exactamente con lo que hay en git — no hizo falta commitear nada, era
puramente un artefacto del checkout, no un problema del repositorio. Vale la pena que el equipo
sepa que esto le puede pasar a cualquiera en Windows: si un `.txt` de modelo "no carga" sin motivo
aparente, mirar primero si tiene `CRLF` (`file archivo.txt`) antes de sospechar del modelo.

## 42. Otro hueco real en el asistente: "los precios de hoy por hora" tampoco se podía responder

Mismo patrón que la nota 40 (la lista de horas negativas): no era el modelo de lenguaje
"negándose" a algo que sabía, era que la herramienta de consulta tenía un bug que la dejaba sin
datos que devolver. Encontrado: la función que trae precio real entre dos fechas construía la
consulta SQL con `BETWEEN fecha_desde AND fecha_hasta`, y en Postgres una fecha sin hora se
interpreta como su medianoche — así que el día `hasta` casi entero quedaba fuera del rango (y
preguntar por un solo día, `desde == hasta`, devolvía prácticamente 0 filas). Confirmado con una
prueba directa: pedir 5 días completos devolvía 97 horas de las 120 esperadas. Corregido en las
tres consultas que compartían el mismo patrón, usando "el día `hasta` completo" como límite en vez
de su medianoche.

Además, no existía ninguna herramienta que devolviera el precio hora a hora **sin resumir** — solo
había percentiles (para patrones históricos) y una lista de horas negativas (solo esas). Se añadió
`precio_tabla_horaria`, y ya se probó con la pregunta exacta que falló en la demo ("una tabla con
los precios de hoy por hora"): responde con la tabla completa del día y un resumen correcto.
