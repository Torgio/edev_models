## Resultados y elección del modelo final

**Tabla X.** Error de los candidatos a producción (MAE en €/MWh; validación 2025, 365 días; test enero a julio de 2026, 212 días).

| Modelo | MAE val | MAE test | Captura del spread (%) | Pico a ±1 h (%) | MAE frente a persistencia (%) |
|---|---|---|---|---|---|
| **Ensemble seleccionado (6 miembros, en producción)** | **11,73** | **11,72** | **92,6** | **76,4** | **−29,9** |
| Ensemble seleccionado con ancla semanal (descartado) | 11,83 | 12,17 | 92,2 | 78,3 | −27,2 |
| Media simple de 8 familias (7 redes y LightGBM por hora) | 12,52 | 12,03 | 90,5 | 77,4 | −28,0 |
| Media simple de 12 familias (las que baten a la persistencia en validación) | 12,63 | 11,88 | 92,9 | 78,3 | −28,9 |
| Red densa (media de 3 semillas) | 12,94 | 13,29 | 88,5 | 74,7 | −20,5 |
| LightGBM global, familia del miembro de mayor peso (media de 3 semillas) | 13,06 | 14,25 | 90,5 | 75,0 | −14,8 |
| GRU (media de 3 semillas) | 14,13 | 13,19 | 89,6 | 77,0 | −21,1 |
| Persistencia (precio de D) | 19,95 | 16,72 | 86,1 | 67,5 | — |

*Notas:* la captura, el pico y la última columna se miden en test; la última es el cambio relativo del MAE de test respecto a la persistencia (negativo indica menos error). Las medias simples usan la mejor semilla de cada familia en validación. Los contrastes del texto usan la semilla elegida en validación, no la media de semillas: LightGBM global 13,01 / 14,16 y GRU 14,05 / 12,89. El MAE de validación de los dos ensembles seleccionados es optimista, porque la selección se optimizó sobre él.

**Cómo se eligió.** Los miembros y sus pesos salen solo de validación. Sobre 2025 se aplicó una selección voraz de ensembles hacia delante y con reemplazo. Los candidatos son la mejor semilla de cada familia en validación; en cada paso entra el que más reduce el MAE de la media, y las veces que entra fijan su peso. Tras 30 pasos quedan seis miembros: LightGBM global (11/30), red densa (10/30), LSTM (3/30), seq2seq de precio absoluto (3/30), GRU (2/30) y seq2seq residual (1/30).

Que el ensemble quede primero en validación (11,73 €/MWh) es consecuencia del procedimiento y no un mérito: ese MAE es optimista, y la medida honesta es la de test. La curva de selección, además, es plana: vale 11,736 en el paso 10 y 11,732 en el 30, donde alcanza el mínimo justo en el límite de pasos. Por eso los pesos pequeños (GRU 2/30; seq2seq residual 1/30, que entra en el paso 17) no están bien determinados.

**Qué dice el test.** En test el ensemble también queda primero (11,72 €/MWh; −29,9 % frente a la persistencia). El contraste diario por remuestreo de bloques móviles de siete días (5000 réplicas, intervalos de percentiles al 95 %) muestra una ventaja clara frente a:
- la persistencia: +5,00 €/MWh [3,93; 6,34];
- LightGBM global (semilla miembro): +2,44 [1,49; 3,46];
- GRU (semilla miembro): +1,16 [0,66; 1,82];
- la variante con ancla semanal: +0,44 [0,14; 0,78].

Con la media de las tres semillas la conclusión no cambia (LightGBM +2,44 [1,47; 3,53]; GRU +0,96 [0,42; 1,72]). Ningún modelo individual sustituye al ensemble, porque este mejora con significación a sus miembros más fuertes. Frente a las medias simples, en cambio, la ventaja en test no es significativa: +0,16 [−0,28; 0,76] frente a la de 12 familias y +0,31 [−0,05; 0,77] frente a la de 8.

**Aportación de los miembros.** Se concentra en dos. Sin LightGBM, el MAE sube a 12,27 en validación y a 12,70 en test; sin la red densa, a 11,98 y 12,18. Los otros cuatro solo aportan en validación: quitar la LSTM baja el test a 11,68 y quitar el seq2seq de precio absoluto, a 11,71. Con los seis miembros a pesos iguales, el MAE es 12,03 en validación y 11,92 en test.

**Selección frente a media simple.** La preferencia por la selección descansa en el criterio de validación fijado, no en una ventaja significativa en test. Hay que declarar además que el método de combinación no se eligió a ciegas respecto al test. La regla anterior promediaba las familias que batían a la persistencia en validación. Con ella entraban Ridge y SARIMAX, que la baten en validación (17,58 y 18,01 frente a 19,95) pero no en test (19,18 y 21,45 frente a 16,72). Esa regla se sustituyó por la selección voraz después de ver ese resultado. Por tanto, el test no es del todo independiente para comparar selección y media simple.

El menor MAE tampoco asegura el mejor orden de las horas. La media de 12 familias acierta algo más el pico (78,3 % frente a 76,4 %). Algunos modelos individuales lo hacen mejor aún, con capturas del spread similares: seq2seq de precio absoluto 81,4 % y Ridge 81,6 %. La selección optimiza el MAE, y ni la captura ni el pico llevan contraste de incertidumbre.

**Advertencia sobre las previsiones de REE.** El histórico de las previsiones de REE para D+1 se cargó con valores ya revisados. Validación y test salen de esa misma carga, así que el MAE de test (11,72 €/MWh) y la mejora del −29,9 % pueden ser optimistas frente a producción, donde solo existe la previsión disponible a las 11:00 de D. Queda pendiente medirlo reentrenando sin esas variables.
