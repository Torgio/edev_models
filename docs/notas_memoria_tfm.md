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
