# Pies de figura · anexo (21-40 y láminas)

*Generado por `11_figuras_anexo.ipynb`. No editar a mano: se regenera.*

**fig21** — `fig21_matriz_correlacion_candidatas.png`

Matriz de correlación de las variables candidatas. El color de la etiqueta indica si el dato está disponible a las 12:00 de D: verde utilizable, rojo con fuga.

**fig22** — `fig22_heatmap_mes_hora.png`

Precio medio en cada combinación de mes y hora. La doble estacionalidad y su interacción en una sola imagen: el valle de mediodía se profundiza en los meses de mayor radiación.

**fig23** — `fig23_patron_ausencias.png`

Correlación entre las indicadoras de ausencia. Un bloque uniforme significa una sola causa: los nulos de ERA5 son su paso trihorario, y los de las tablas diarias, el aterrizaje en la hora 00 local. No son huecos de datos.

**fig24** — `fig24_calendario_precio.png`

Calendario de precios medios diarios. Aísla episodios que una serie temporal larga aplasta: crisis de precios, olas de calor y el apagón ibérico se leen como manchas.

**fig25** — `fig25_perfil_semanal.png`

Perfil horario del precio según el día de la semana. Un fin de semana con forma distinta —y no sólo nivel distinto— exige interacción entre día y hora en el modelo.

**fig26** — `fig26_dendrograma.png`

Agrupamiento jerárquico de las columnas por similitud. Los grupos que se cierran por debajo de |r| = 0,90 contienen variantes de la misma información.

**fig27** — `fig27_pca_varianza.png`

Varianza explicada por componente. Hacen falta 17 componentes para el 90 % de la varianza de 77 columnas: la dimensionalidad efectiva es mucho menor que el número de variables.

**fig28** — `fig28_pca_cargas.png`

Peso de cada variable en las dos primeras componentes. Variables próximas entre sí aportan información parecida; una componente que mezcla familias distintas no es utilizable como feature porque pierde la etiqueta de fuga.

**fig29** — `fig29_cramer_vs_pearson.png`

V de Cramér frente a correlación de Pearson. Una barra de color mucho más larga que la gris señala una relación no lineal, que un modelo lineal desaprovecharía.

**fig30** — `fig30_histogramas_candidatas.png`

Distribución de cada variable candidata. El color del título y de los ejes indica su disponibilidad a las 12:00 de D.

**fig31** — `fig31_cajas_por_familia.png`

Diagramas de caja agrupados por escala. El relleno indica la etiqueta de fuga: verde utilizable, rojo con fuga, ámbar con desfase.

**fig32** — `fig32_qq_precio.png`

Gráfico cuantil-cuantil del precio contra la normal (asimetría 1.19). La separación de la diagonal en los extremos confirma colas más pesadas que las de una normal, lo que condiciona la elección de métrica.

**fig33** — `fig33_curva_duracion_precios.png`

Curva de duración: precios ordenados de mayor a menor. Los extremos de cada curva son las horas que deciden el arbitraje; la parte central, el régimen habitual.

**fig34** — `fig34_acf_pacf.png`

Funciones de autocorrelación total y parcial del precio diario. El pico semanal y el decaimiento lento indican estructura autorregresiva con componente de calendario.

**fig35** — `fig35_descomposicion_stl.png`

Descomposición en tendencia, estacionalidad semanal y residuo. El residuo concentra el 13 % de la varianza: es la parte que las features exógenas tienen que explicar.

**fig36** — `fig36_densidades_regimen.png`

Densidad del precio en cada régimen. No difieren sólo en nivel: cambian la dispersión y la forma, que es el argumento para tratarlos por separado y no promediarlos.

**fig37** — `fig37_violines_hora.png`

Distribución completa del precio en cada hora. Las horas centrales muestran una masa en precios muy bajos que la media por sí sola no revela.

**fig38** — `fig38_correlacion_por_anio.png`

Estabilidad de cada relación a lo largo del tiempo. Una fila que cambia de signo o de intensidad señala una relación que no generaliza fuera de su período.

**fig39** — `fig39_dispersion_sin_fuga.png`

Precio frente a los drivers utilizables, coloreado por hora del día. El color revela que la misma variable significa cosas distintas según la hora: la relación es condicionada, no simple.

**fig40** — `fig40_coste_confusion.png`

Coste de cada tipo de error. Confundir una hora cara con una barata cuesta 61.2 EUR/MWh, 1.9 veces más que confundirla con una intermedia: la métrica de evaluación no puede ser simétrica.

**L1** — `L1_lamina1_target.png`

Cuatro vistas del precio: evolución diaria, distribución en escala logarítmica, perfil horario por año y curva de duración. En conjunto muestran que el problema cambió de naturaleza durante el propio período de entrenamiento.

**L2** — `L2_lamina2_estacionalidad.png`

Mapas de calor mes×hora y día×hora, con sus perfiles marginales. La interacción entre ciclos es lo que obliga a que el modelo trate las 24 horas por separado.

**L3** — `L3_lamina3_estructura.png`

Matriz de correlación, varianza por componente principal, asociación no lineal y estabilidad año a año. Juntas justifican qué variables sobran y cuáles no generalizan.

**L4** — `L4_lamina4_calidad.png`

Los nulos de las tablas diarias y de ERA5 son estructurales, no huecos. Los días con 23 y 25 horas corresponden exactamente a los cambios de horario.

**L5** — `L5_lamina5_bess.png`

Cuándo caen las horas extremas, cuánto separa el precio a cada clase, cómo evoluciona el diferencial aprovechable y cuánto cuesta cada tipo de error. Es el puente entre el EDA del precio y la optimización del almacenamiento.

