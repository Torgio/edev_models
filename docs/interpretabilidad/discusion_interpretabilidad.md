## Discusión de los resultados: explicatividad e interpretabilidad

**Por qué no hay coeficientes que leer.** El sistema en producción es una media ponderada de seis modelos distintos: un LightGBM, una red densa, una LSTM, una GRU y dos seq2seq. Reciben la información en tres formatos (matriz plana, ventana secuencial de 168 horas y vista resumida) y persiguen dos objetivos (el precio absoluto o el residuo respecto al precio de D). Un sistema así no se puede interpretar por coeficientes, y la explicación tiene que ser posterior al entrenamiento. Se da en dos niveles: una permutación por grupos, agnóstica al modelo, para el sistema completo, y TreeSHAP, específico de árboles, para su miembro de mayor peso.

**Qué gobierna la predicción del sistema.** Se permutaron entre sí los 212 días de test, grupo a grupo, con la misma permutación en todos los formatos de entrada. Cada día recibe del día donante sus 24 horas y, en las redes, también su ventana de 168 horas. El ancla del residuo se mantuvo fija, y se hicieron tres permutaciones aleatorias sobre el mismo modelo (Figura X). Partiendo de un MAE de 11,72 €/MWh, dominan las previsiones de REE para D+1: permutarlas lo eleva en 10,10 €/MWh (+86,2 %). Muy por detrás quedan:
- el precio español de D, D-1 y D-6: +1,69;
- los precios europeos, que incluyen el portugués y los diferenciales: +1,25;
- los programas de D: +1,11;
- la generación y los flujos reales de D-1 y D-6: +0,94;
- la meteorología prevista: +0,85;
- el calendario: +0,84.

Dentro del test, permutar el gas apenas cambia el error (+0,13) y en las capacidades no hay efecto medible. Son variables lentas y el test abarca siete meses, así que esto no indica que sean irrelevantes entre regímenes.

La cifra de las previsiones de REE debe leerse con cautela. Su histórico se cargó con valores ya revisados, de modo que tanto este +10,10 como el MAE de test pueden ser optimistas frente a producción. No se ha medido reentrenando sin ellas.

![Aumento del MAE de test del ensemble seleccionado (base 11,72 €/MWh; 212 días de enero a julio de 2026) al permutar entre días cada grupo de variables, a la vez en todos los formatos de entrada. Cada día recibe del día donante sus 24 horas y, en las redes, su ventana de 168 h. La barra es la media de tres permutaciones aleatorias de los días sobre el mismo modelo, y los bigotes marcan su mínimo y su máximo. El grupo del precio español reúne D, D-1 y D-6; el precio portugués y los diferenciales están en el de precios europeos. El ancla del residuo se mantiene fija; la barra rayada y cortada muestra, como referencia, el efecto de permutar también el ancla (+20,36 €/MWh).](fig_permutacion_ensemble.png)

**Cómo leer el precio de D en la permutación.** El +1,69 no mide cuánto depende el sistema del precio de D, por tres motivos:
- El grupo del precio español solo contiene es_esios_D, D-1 y D-6. El precio portugués y los diferenciales quedan intactos en otro grupo, y con ellos se reconstruye el español (el diferencial es exactamente ES − PT en el 99,998 % de las filas). La permutación crea además combinaciones con PT ≠ ES casi inexistentes en entrenamiento. Lo mismo vale para el +1,25 de los precios europeos.
- Los errores de los miembros se compensan al combinarlos. La media ponderada de sus aumentos es +5,65, frente al +1,69 del ensemble. LightGBM, que no tiene ancla, pierde 10,46 €/MWh; la red densa, 3,80, y el seq2seq de precio absoluto, 3,26.
- Las redes residuales conservan el ancla. Si se permuta también, el ensemble pierde 20,36 €/MWh.

Medir la dependencia real exigiría permutar un bloque conjunto (precios español y portugués, diferenciales y retardos), y eso no se ha calculado.

**El miembro de mayor peso.** En LightGBM (11/30), los valores SHAP de test reparten el |SHAP| medio por grupos así: previsiones de REE 25,5 %, precio español 19,1 %, precios europeos 18,2 % y programas de D 12,8 %. En validación el reparto es casi igual (24,3 %, 20,1 % y 18,7 % en los tres primeros). Por variable, la correlación de rangos entre validación y test es 0,979. Los cuatro primeros grupos son los mismos que en la permutación del ensemble, pero con pesos relativos muy distintos: en la permutación, REE pesa unas seis veces el precio español; en SHAP, 1,3 veces.

La primera variable individual es el precio portugués de D (19,10 €/MWh de |SHAP| medio), por delante del español (12,13), pero esa separación no se puede interpretar. Los dos precios correlacionan 0,997 y coinciden en el 93,8 % de las horas de 2020-2026; en test, 0,988 y 83,4 %, con tendencia descendente. Sus SHAP correlacionan 0,814. Permutar solo el portugués eleva el MAE de LightGBM en 5,65 €/MWh y solo el español, en 4,56; los dos a la vez, en 12,38, más que la suma. Como permutar uno solo crea combinaciones casi inexistentes en entrenamiento, los efectos individuales llevan extrapolación, y la cifra fiable es la del bloque conjunto. El reparto no se explica por los huecos imputados, que solo difieren en una hora, ni por la correlación con el objetivo (0,911 y 0,910).

**Contraste con el EDA.** La asociación de Spearman del análisis exploratorio anticipaba mal la importancia SHAP: la correlación de rangos entre |rho| y |SHAP| de test es 0,314, y solo 5 de las 12 primeras variables coinciden. Coinciden los precios de D, sus retardos y el gas. Discrepan las previsiones de REE, con |rho| entre 0,19 y 0,23 (puestos 34 a 43) y puestos 2, 4 y 6 en SHAP. Podría deberse a que informan del cambio respecto a D una vez conocido su precio, más que del nivel, o a relaciones no monótonas o que cambian con la hora, que Spearman no capta. No se ha comprobado, y su importancia SHAP puede estar además inflada por el histórico revisado.

Sobre ese ranking hay dos precisiones:
- Incluye 2025, el año de validación.
- Sus etiquetas de frontera no siguen la convención final. Marcan el precio español de D como objetivo encubierto y los programas de D como posteriores, pero el precio de D se casa a las 13:00 de D-1 y el PBF se publica a las 13:45, antes de las 11:00 de D. En la matriz, es_esios_D coincide con el objetivo del día anterior en el 99,92 % de las filas.

**Comportamiento.** El ensemble se mantiene cerca de la forma de D: su perfil horario se parece más al de D (correlación 0,938 en test) que el perfil real (0,858). La magnitud media de su movimiento respecto a D es el 82,6 % de la del cambio real, con pendiente 0,59 y correlación del cambio 0,79.

Por cuartil del cambio real (Figura Y), en el cuartil más tranquilo el ensemble es peor que la persistencia (8,30 frente a 6,37 €/MWh; +30,2 %). En el de mayor cambio reduce el error un 45,8 % (16,31 frente a 30,09). Los cuartiles se definen con el cambio real medio |D+1 − D|, que es por construcción el MAE de la persistencia en cada cuartil. El patrón es, por tanto, descriptivo y en buena parte consecuencia de esa definición.

Escalar el movimiento no mejora el resultado: el factor ajustado en validación (1,138) empeora el test de 11,72 a 11,97 €/MWh. El encogimiento hacia D es la prudencia esperable en un pronóstico con incertidumbre, no un sesgo que se corrija escalando.

![(a) MAE de test por cuartil del cambio real medio diario |D+1 − D| (53 días por cuartil) para la persistencia (gris) y el ensemble seleccionado (azul); por construcción, el MAE de la persistencia en cada cuartil es el cambio medio del cuartil. (b) Lunes 23/03/2026: precio de D (gris discontinuo), real (negro) y ensemble (azul). Es el día del cuartil de mayor cambio cuyo cociente entre el error del ensemble y el de la persistencia es la mediana del cuartil (MAE 12,80 frente a 25,24 €/MWh). Ese día el ensemble sobrestima la madrugada y se queda corto en el pico de la tarde.](fig_comportamiento.png)

**Consecuencias para un operador de batería.** A un operador de batería le importa el orden de las horas. El ensemble captura el 92,6 % del spread diario y sitúa el pico a ±1 h en el 76,4 % de los días, frente al 86,1 % y el 67,5 % de la persistencia. En el cuartil de mayor cambio mantiene la ventaja (89,5 % y 75,5 %, frente a 81,9 % y 62,3 %).

La selección, sin embargo, optimiza el MAE. En acierto del pico hay modelos individuales mejores (seq2seq de precio absoluto 81,4 %, Ridge 81,6 %), y estas métricas no llevan contraste de incertidumbre. Si el uso principal es el arbitraje, convendría un criterio de selección específico.

El operador debe asumir dos cosas:
- El cambio previsto respecto a D es más contenido que el real.
- La calidad depende de las previsiones de REE. Con la previsión sin revisar disponible a las 11:00, el error en producción puede ser mayor que el medido.

**Limitaciones del análisis.**
- **Ganancia y SHAP miden cosas distintas.** La ganancia de LightGBM (media de tres semillas, en entrenamiento) atribuye el 85,9 % a los precios, frente al 37,3 % del |SHAP| en test de la semilla en producción. La ganancia mide la reducción del error cuadrático en entrenamiento y pesa más las contribuciones grandes: con SHAP² en entrenamiento, los precios pasan del 47,3 % al 74,5 %. No mide lo mismo que el |SHAP| en test.
- **Grupos correlacionados.** Con grupos correlacionados, y con el precio español y el portugués en grupos distintos, la permutación deja que unos grupos compensen a otros y crea combinaciones poco realistas. Los aumentos no se pueden sumar.
- **Periodo y repeticiones.** Siete meses de test infravaloran las variables que cambian despacio (gas, capacidades), y tres permutaciones solo dan rangos orientativos.
- **No son medidas causales.** Ni SHAP ni la permutación lo son. Con variables casi colineales, el reparto del mérito depende de la estructura de cada modelo.
