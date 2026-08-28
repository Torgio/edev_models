# Pies de figura

*Generado por `eda/figuras_memoria.py`. No editar a mano: se regenera.*

**Figura 1** — `fig01_precio_diario_regimenes.png`

Precio medio diario. La excepción ibérica promedia 102.63 EUR/MWh frente a 81.05 en régimen normal.

**Figura 2** — `fig02_distribucion_precio.png`

Distribución del precio horario. Asimetría 1.19 (cola más larga por la derecha) y curtosis 2.74. Las dos colas importan por motivos distintos: la alta domina el error medio, la baja el valor del arbitraje.

**Figura 3** — `fig03_rangos_precio_anio.png`

Reparto por rango de precio. Las horas de precio negativo pasan de 0 en 2020 a 665 en 2026: un fenómeno nuevo, no una cola de la distribución.

**Figura 4** — `fig04_perfil_horario_precio_anio.png`

Perfil horario por año. La hora del mínimo se desplaza de las 4h a las 13h y la amplitud crece de 13 a 103 EUR/MWh: la estructura horaria que el modelo debe aprender NO es estacionaria.

**Figura 5** — `fig05_autocorrelacion_precio.png`

Autocorrelación del precio. El valor de hace 24 h explica r=0.911 del actual: es la vara mínima que cualquier feature exógena tiene que superar.

**Figura 6** — `fig06_ranking_drivers_fuga.png`

Correlación de cada driver con el precio. El color indica si el dato existe a las 12:00 de D, cuando hay que emitir la predicción: casi todo lo que encabeza el ranking es inutilizable en producción.

**Figura 7** — `fig07_precio_vs_gas_regimen.png`

Precio frente al gas. El ratio mediano cae de 2.58 en régimen normal a 1.74 bajo el tope al gas: dos regímenes distintos, no ruido.

**Figura 8** — `fig08_separacion_horas_caras.png`

Separación entre horas caras y baratas. El mejor driver utilizable alcanza d=1.40 frente a 2.20 del mejor con fuga. La eólica prevista apenas separa (d=0.06) pese a mover el nivel del precio.

**Figura 9** — `fig09_roc_hora_cara.png`

Curvas ROC para clasificar una hora como cara. El AUC no depende del umbral, así que permite comparar drivers con escalas distintas.

**Figura 10** — `fig10_frontera_fuga.png`

Clasificación de cada tabla según esté disponible en el momento de emitir la predicción. Es la tabla que gobierna la selección de variables.

**Figura 11** — `fig11_acoplamiento_precios.png`

Porcentaje de horas con precio idéntico al del país vecino. Con Portugal verifica el supuesto de zona única peninsular; con Francia mide cuánta información del precio francés es independiente.

**Figura 12** — `fig12_correlacion_por_hora.png`

Correlación driver-precio hora a hora. Una amplitud grande significa que la correlación única del ranking global no describe ninguna hora concreta: hace falta interacción con la hora del día.

**Figura 13** — `fig13_correlacion_desfase.png`

Correlación del precio con cada driver desplazado en el tiempo. El desfase que maximiza la asociación es el lag que entra como feature, y permite aprovechar variables cuya versión contemporánea tiene fuga.

**Figura 14** — `fig14_error_previsiones_hora.png`

Error absoluto medio de las previsiones publicadas por REE, que son las únicas features disponibles a las 12:00 de D. El modelo hereda este error entero: fija un suelo al que ningún algoritmo puede bajar.

**Figura 15** — `fig15_error_previsiones_mes.png`

Estacionalidad del error de previsión. Los meses de mayor error coinciden con los de mayor dificultad de predicción del precio: explica de antemano dónde fallará el modelo.

**Figura 16** — `fig16_cobertura_renovable.png`

Fracción de la demanda cubierta por renovable variable. La solar pasa del 6.6% al 25.8%: es la causa física del cambio de estructura horaria del precio.

**Figura 17** — `fig17_autoconsumo_quiebre.png`

Diferencia entre las series de ESIOS y las de ENTSO-E, por demanda y por generación. Ambas rompen en diciembre de 2025 con la misma magnitud: es el mismo fenómeno medido dos veces, y descarta usar las series de ESIOS directas.

**Figura 18** — `fig18_offset_gas_fuentes.png`

Diferencia entre el agregado de gas de ENTSO-E y la suma de ESIOS. Cae de -806 a -192 MW: el desfase no es estable, así que no se puede usar una fuente en entrenamiento y otra en test.

**Figura 19** — `fig19_arbitraje_techo_vs_regla.png`

Ingreso de arbitraje diario. Una regla fija —descargar siempre a las mismas horas— captura el 71.8% del máximo teórico. Ese margen restante es todo lo que un modelo puede aportar, y la referencia contra la que debe evaluarse.

**Figura 20** — `fig20_probabilidad_hora_cara.png`

Distribución horaria de las horas extremas. Descargar en [19, 20, 21, 22] acierta el 62.5% de las veces frente al 16.7% del azar (×3.75).

