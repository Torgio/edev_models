# Pies de lámina

*Generado por `12_laminas_tfm.ipynb`. No editar a mano: se regenera.*

## Lámina 1 — `L01_target.png`

**El precio: nivel, colas, composición y duración**

Cuatro vistas del target. La escala logarítmica del segundo panel revela unas colas que la lineal esconde; el tercero muestra que los precios negativos aparecen a mitad del histórico y crecen cada año. El problema cambió de naturaleza durante el propio período de entrenamiento, lo que condiciona cómo se pondera el histórico.

## Lámina 2 — `L02_estructura_temporal.png`

**Las estacionalidades del precio y su desplazamiento**

El perfil horario por año (arriba izquierda) es el resultado central del análisis: la hora del mínimo se desplaza hacia el mediodía y la amplitud entre máximo y mínimo se multiplica. Los mapas de calor separan el ciclo anual del semanal, y los violines añaden la forma completa de la distribución en cada hora.

## Lámina 3 — `L03_persistencia.png`

**Persistencia: cuánto del precio explica su propio pasado**

Funciones de autocorrelación total y parcial del precio diario, autocorrelación horaria hasta ocho días —donde los múltiplos de 24 h destacan— y evolución del diferencial diario. La persistencia fija la vara mínima: una feature exógena sólo se justifica si aporta por encima de lo que ya da el precio de ayer.

## Lámina 4 — `L04_formacion_precio.png`

**La formación del precio: orden de mérito, gas y horas nulas**

La curva de oferta empírica (arriba izquierda) es el orden de mérito deducido del dato: su desplazamiento entre años mide el cambio del parque de generación. Los paneles del gas muestran el desacople provocado por el tope, y el último localiza en el calendario las horas de precio nulo o negativo.

## Lámina 5 — `L05_drivers_fuga.png`

**Qué explica el precio y qué de ello existe al predecir**

El ranking de correlación coloreado por disponibilidad a las 12:00 de D muestra que casi todos los drivers más correlacionados son inutilizables en producción. Los otros paneles matizan esa correlación única: cambia según la hora, no siempre se sostiene entre años, y una parte de la asociación es no lineal y Pearson la infravalora.

## Lámina 6 — `L06_discriminacion.png`

**Capacidad de los drivers para decidir cuándo operar la batería**

Evaluación de los drivers como clasificadores de horas caras y baratas, que es la decisión que toma un sistema de almacenamiento. La matriz de coste traduce el error a euros: confundir una hora cara con una barata cuesta el diferencial entero, y una métrica simétrica como el MAE no lo recoge.

## Lámina 7 — `L07_calidad_previsiones.png`

**El error que el modelo hereda de sus propias entradas**

Las únicas variables disponibles a las 12:00 de D son previsiones, y su error marca un suelo que ningún algoritmo puede rebasar. El panel del sesgo separa la parte corregible —un desvío sistemático se descuenta antes de entrenar— de la parte que es ruido puro.

## Lámina 8 — `L08_calidad_datos.png`

**Calidad de los datos: qué hueco es estructural y qué hueco es real**

Los nulos elevados de las tablas diarias y de ERA5 son consecuencia del diseño de la unión, no huecos de datos: el patrón de ausencias lo demuestra al mostrar bloques uniformes. Los días con 23 y 25 horas corresponden exactamente a los cambios de horario, lo que valida cualquier agregación diaria posterior.

## Lámina 9 — `L09_decisiones_fuente.png`

**Cuando dos fuentes miden lo mismo: qué columna entra al modelo**

Cuatro decisiones de fuente con su evidencia. El autoconsumo rompe las series de ESIOS por demanda y por generación el mismo mes y con la misma magnitud. El agregado de gas de ENTSO-E mezcla dos tecnologías con un desfase que no es estable. La eólica sí es intercambiable entre fuentes. Y la hidráulica de ESIOS mezcla generación con bombeo, como delata su concentración de valores negativos al mediodía.

## Lámina 10 — `L10_vara_referencia.png`

**El listón: cuánto arbitraje captura ya una regla trivial**

Una regla fija —descargar y cargar siempre a las mismas horas— captura el 71.8 % del arbitraje máximo teórico. El margen restante es todo lo que un modelo puede aportar, y la referencia contra la que debe evaluarse. Es también el motivo de medir el rendimiento en euros y no sólo en error medio.

