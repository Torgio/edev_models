# Selección de figuras para la memoria · máximo 20

**28-ago-2026** · Basado en los resultados reales de los notebooks 02 a 09.

## El criterio

Veinte páginas y veinte imágenes obligan a una regla: **cada figura tiene que responder a una
pregunta del hilo argumental, y ninguna puede repetir lo que ya dijo otra**. Una figura bonita
que no cambia ninguna decisión, fuera.

El hilo que se propone tiene cinco pasos, y es el mismo que sigue el proyecto:

1. **Qué hay que predecir** — cómo es el precio y por qué es difícil
2. **Qué lo explica** — los drivers, ordenados por evidencia
3. **Qué de eso se puede usar** — la frontera de fuga, que descarta casi todo lo del paso 2
4. **Cómo de buenas son las entradas que sí quedan** — el suelo de error del modelo
5. **Contra qué se compara** — la vara que un modelo tiene que superar

Los pasos 3 y 5 son los que distinguen este TFM de un ejercicio de correlaciones. Ahí es donde
conviene gastar figuras.

---

## Las 20, por orden de aparición

### Bloque 1 · Qué hay que predecir (5 figuras)

| # | Figura | Origen | El número que lleva |
|---|---|---|---|
| **1** | Serie del precio diario con los regímenes marcados | 03 · F.4 | Excepción ibérica 102,63 €/MWh de media frente a 81,06 en régimen normal; apagón 14,19 |
| **2** | Distribución del precio, escala normal y log | 03 · F.2 | Asimetría 1,19 · curtosis 2,74 · rango [−15, 700] |
| **3** | Reparto de horas por rango de precio y año | 03 · F.2 | **Precios negativos: 0 en 2020-2023, 247 en 2024, 555 en 2025, 665 en 2026** |
| **4** | Perfil horario del precio por año | 03 · F.3 | **La hora del mínimo pasa de las 4h a las 13h; la amplitud de 13 a 103 €/MWh** |
| **5** | Autocorrelación horaria hasta 8 días | 03 · F.5 | r(D−1) = 0,911 horario, 0,941 diario |

**Por qué estas cinco.** La 3 y la 4 son las más fuertes de todo el EDA y van juntas: enseñan
que **el problema ha cambiado de naturaleza durante el propio período de entrenamiento**. Un
modelo que aprenda del histórico completo aprende una estructura horaria que ya no existe. Eso
justifica de golpe ponderar los años recientes, incluir la capacidad FV instalada como regresor,
y tratar los precios negativos como un fenómeno propio y no como cola.

La 5 fija la vara: si el precio de ayer explica el 91 % del de hoy, cualquier feature exógena
tiene que aportar **por encima de eso**.

**Se queda fuera:** la banda diaria máx-mín (F.6), aunque el dato es bueno (diferencial de 16,9
a 114,0 €/MWh, y días con mínimo negativo de 0 a 108). Se cita en texto y su contenido reaparece
en la figura 19, que además lo conecta con el BESS.

---

### Bloque 2 · Qué explica el precio (4 figuras)

| # | Figura | Origen | El número que lleva |
|---|---|---|---|
| **6** | Ranking de correlación con el precio, coloreado por etiqueta de fuga | 03 · G | Gas MIBGAS 0,880 · Francia 0,872 · demanda residual prevista 0,439 |
| **7** | Precio frente al gas, separado por régimen | 03 · G.1 | Ratio precio/gas: **2,58 en normal, 1,74 bajo la excepción ibérica** |
| **8** | Distribución de cada driver según la clase de hora | 09 · B | d de Cohen: hidráulica 2,20 · solar prevista **1,40** · eólica prevista 0,06 |
| **9** | Curvas ROC de los drivers para «hora cara» | 09 · D | AUC hidráulica 0,837 · demanda prevista **0,708** · eólica 0,511 |

**Por qué estas cuatro.** La 6 es la figura-tesis del proyecto: enseña a la vez qué correlaciona
y **de qué color es** cada barra. Casi todo lo que encabeza el ranking está en rojo.

La 7 justifica con una sola cifra por qué la excepción ibérica se trata aparte: el ratio
precio/gas cae de 2,58 a 1,74, o sea que el tope al gas desacopló las dos series. No es una
observación extrema, es otro proceso.

La 8 y la 9 traen algo que la correlación no da: la **eólica prevista no separa horas caras de
baratas** (d = 0,06, AUC = 0,511), mientras la solar sí (d = 1,40). Contraintuitivo y muy
citable: la eólica mueve el nivel del precio, pero no decide qué horas del día son las caras.

---

### Bloque 3 · Qué se puede usar de verdad (4 figuras)

| # | Figura | Origen | El número que lleva |
|---|---|---|---|
| **10** | Tabla de la frontera de fuga por tabla | 03 · bloque 0 | 10 tablas clasificadas: sin fuga, con fuga, con desfase, condicional |
| **11** | Acoplamiento de precios ES-PT y ES-FR por mes | 05 · M.4 | Cuántas horas están acopladas y cuántas hay congestión |
| **12** | Correlación por hora del día de cada driver | 08 · C | La amplitud dice qué drivers necesitan interacción con la hora |
| **13** | Correlación cruzada con desfase | 08 · B | El lag que maximiza cada relación es el lag que entra como feature |

**Por qué estas cuatro.** La 10 no es un gráfico, es la tabla que gobierna todo lo demás; sin
ella, las figuras 6 a 9 se leen mal. La 11 verifica un supuesto que el TFM asume sin haberlo
comprobado: que el perímetro peninsular es una sola zona de precio.

La 12 y la 13 son las dos únicas del bloque temporal que se salvan del recorte, y por un motivo
concreto: convierten variables con fuga en features utilizables. Una serie que no se puede usar
contemporánea sí se puede usar retardada, y el desfase óptimo lo dice la figura 13.

**Se queda fuera:** la matriz de correlación completa reordenada (07 · A.2). Es vistosa pero
ocupa media página para decir «hay bloques redundantes», y eso cabe en una frase. Los grupos de
redundancia se citan en texto.

---

### Bloque 4 · Cómo de buenas son las entradas (3 figuras)

| # | Figura | Origen | El número que lleva |
|---|---|---|---|
| **14** | MAE de la previsión de REE por hora local | 03 · H.1 | Demanda 257 MW · eólica 772 MW · solar 547 MW |
| **15** | MAE de la previsión por mes | 03 · H.1 | Dónde falla la entrada coincide con dónde fallará el modelo |
| **16** | Cobertura de la demanda por eólica y FV | 04 · L.4 | El cambio estructural que explica las figuras 3 y 4 |

**Por qué estas tres.** Es el bloque que **no existía** antes de este EDA y probablemente el de
mayor valor añadido. Las features sin fuga son previsiones, y una previsión trae su propio
error: si la eólica de REE se equivoca 772 MW de media y **sobreestima sistemáticamente en 128,5
MW**, el modelo hereda ese error entero. Ningún algoritmo recupera información que la entrada no
tiene.

El sesgo es además accionable: un sesgo constante se corrige antes de entrenar; el ruido no.

La 16 cierra el círculo con las figuras 3 y 4: enseña la causa física del cambio de estructura.

---

### Bloque 5 · Decisiones de datos que condicionan el modelado (2 figuras)

| # | Figura | Origen | El número que lleva |
|---|---|---|---|
| **17** | Serie mensual del autoconsumo con el quiebre marcado | 02 · A.2/A.3 | Salto en dic-2025 de ~0 a 434,7 MW · **correlación 0,9984 entre los dos lados del balance** |
| **18** | Offset anual del gas de ENTSO-E frente a la suma de ESIOS | 02 · A.1 | Cae de −806 a −192 MW · correlación 0,996 contra la suma, 0,979 contra el CCGT |

**Por qué solo dos, de todo el notebook 02.** El EDA de calidad produjo mucho más —los ceros de
`entsoe_load` con sus 72 reintentos en el log, la convención NULL/0 del bombeo, el DST íntegro,
las discontinuidades de ERA5— pero **casi todo se cuenta mejor en texto que en figura**. Estas
dos se ganan el sitio porque cada una cambia qué columna entra al modelo:

- la 17 descarta `ree_load` y `ree_gsolar_mw`, y el 0,9984 es la prueba de que es un solo
  fenómeno medido dos veces;
- la 18 descarta `entsoe_gas_mw` y demuestra que el offset no es estable, así que no se puede
  usar una fuente en train y otra en test.

---

### Bloque 6 · Contra qué se compara el modelo (2 figuras)

| # | Figura | Origen | El número que lleva |
|---|---|---|---|
| **19** | Ingreso de arbitraje: techo teórico frente a regla horaria fija | 09 · E | **244,59 €/día perfecto · 175,61 la regla fija · 71,8 % del techo** |
| **20** | Probabilidad de ser hora cara o barata, por hora del día | 09 · A | La regla fija acierta el 62,5 % frente al 16,7 % del azar (×3,75) |

**Por qué cierran la memoria.** La 19 es la figura más importante del trabajo después de la 4.
Dice, en euros, **cuánto tiene que mejorar el modelo para justificar su existencia**: una regla
tan tonta como «descarga siempre a las 19-22 y carga a las 14-16» ya captura el 71,8 % del
arbitraje posible. Ese 28,2 % restante es todo el margen que hay.

Es también el argumento de por qué el MAE no basta como métrica: la matriz de coste del mismo
bloque enseña que confundir una hora cara con una barata cuesta 61,15 €/MWh, y confundirla con
una intermedia 32,60 — **la mitad**. Una métrica simétrica no recoge eso.

---

## Lo que se queda fuera, y por qué

Merece una frase en la memoria decir qué se hizo y no se muestra: demuestra que la selección es
deliberada.

| Análisis | Por qué no entra | Dónde se menciona |
|---|---|---|
| Matriz de correlación completa (77×77) | Media página para decir «hay redundancia» | Una frase + los grupos en texto |
| Dendrograma de agrupamiento | Ídem, y con 77 etiquetas es ilegible impreso | Anexo, si acaso |
| PCA y varianza acumulada | Diagnóstico interno; las componentes no son features porque pierden la etiqueta de fuga | Una frase |
| Mapa de nulos y cobertura | El 95,8 % de las tablas diarias es 23/24 por diseño: se explica en dos líneas | Texto |
| Cobertura y bordes de DST | Verificación que salió limpia: 13 días, 7 de 23h y 6 de 25h, cero horas ausentes | Texto |
| Ceros espurios de `entsoe_load` | 9 horas de 58.319 con causa documentada en `pipeline_log` | Texto, en limitaciones |
| Discontinuidades de ERA5 | Una reparable, la otra verificada como no metodológica | Texto, en limitaciones |
| V de Cramér frente a Pearson | Interesante metodológicamente, pero no cambia ninguna decisión final | Anexo |
| Balance de energía, potencia instalada, interconexiones | Contexto del sistema: valioso pero no argumenta la selección de variables | 2-3 frases con cifras |
| Correlación móvil y parcial | Refinan el análisis, no lo redirigen | Anexo |

---

## Cuatro resultados que van en texto y pesan tanto como una figura

Son cifras que no necesitan gráfico y que conviene no perder:

**Las tres fuentes españolas del precio son idénticas.** Correlación 1,0000, diferencia máxima
0,000 entre OMIE y ESIOS, y 85 horas con diferencia superior a 0,01 frente a ENTSO-E. Se elige
`spot_es_esios` por tener menos nulos (6 frente a 30). Decisión cerrada con una línea.

**El target no está censurado.** El rango [−15, 700] parecía un límite administrativo, pero solo
4 horas tocan el suelo y 1 el techo, en años distintos. Es el extremo real observado. La
sospecha se comprueba y se descarta, que es exactamente cómo debe funcionar.

**La meteorología da una cota superior optimista.** `ecmwf_forecast_agg` tiene 168 filas: es una
ventana móvil de inferencia, no histórico. Sin previsión meteorológica histórica no se puede
entrenar con la meteo que existirá en producción, solo con ERA5, que es reanálisis. **Hay que
declararlo**, porque es el tipo de limitación que aparece en la defensa si no la dices tú.

**La duplicación ×24 sobrevivió a un EDA entero.** El parquet tenía 1.393.183 filas donde debía
haber 58.319, y nadie lo detectó porque los porcentajes y las correlaciones sobreviven a una
duplicación uniforme. Solo fallaban los conteos absolutos, y no se contrastaban contra nada. Es
una buena anécdota metodológica para el apartado de calidad de datos, y justifica el guardarraíl
que ahora corre en cada unión.

---

## Reparto orientativo de las 20 páginas

| Apartado | Páginas | Figuras |
|---|---|---|
| Introducción al EDA y arquitectura de la capa bronce | 2 | — |
| El target: qué hay que predecir | 4 | 1-5 |
| Qué explica el precio | 4 | 6-9 |
| La frontera de fuga y qué queda utilizable | 4 | 10-13 |
| Calidad de las entradas disponibles | 3 | 14-16 |
| Decisiones de datos | 2 | 17-18 |
| La vara de referencia | 1 | 19-20 |

Las figuras 4, 6, 14 y 19 son las cuatro que no pueden faltar si hubiera que recortar más: el
cambio estructural del problema, la frontera de fuga, el suelo de error heredado de las
previsiones, y el listón económico que el modelo tiene que superar.
