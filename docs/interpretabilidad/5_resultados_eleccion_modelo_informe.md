## Resultados y elección del modelo final

**Tabla X.** Candidatos a modelo final.

| Modelo | MAE val | MAE test | Captura (%) | Pico ±1 h (%) | Mejora frente a persistencia (%) |
|---|---|---|---|---|---|
| **Ensemble seleccionado (modelo final)** | **11,73** | **11,72** | **92,6** | **76,4** | **29,9** |
| Ensemble seleccionado con ancla semanal (descartado) | 11,83 | 12,17 | 92,2 | 78,3 | 27,2 |
| Media simple de 8 familias | 12,52 | 12,03 | 90,5 | 77,4 | 28,0 |
| Media simple de 12 familias (regla anterior) | 12,63 | 11,88 | 92,9 | 78,3 | 28,9 |
| Red densa | 12,94 | 13,29 | 88,5 | 74,7 | 20,5 |
| LightGBM global | 13,06 | 14,25 | 90,5 | 75,0 | 14,8 |
| GRU | 14,13 | 13,19 | 89,6 | 77,0 | 21,1 |
| Persistencia (precio de D) | 19,95 | 16,72 | 86,1 | 67,5 | — |

*Nota:* MAE en €/MWh; validación 2025 (365 días), test enero-julio de 2026 (212 días). Captura del spread, pico y mejora (reducción relativa del MAE), en test. Modelos individuales: media de 3 semillas; medias simples: mejor semilla de cada familia.

**Elección.** Miembros y pesos se fijaron solo con validación, mediante selección voraz hacia delante con reemplazo en 30 pasos: LightGBM global (11/30), red densa (10/30), LSTM (3/30), seq2seq de precio absoluto (3/30), GRU (2/30) y seq2seq residual (1/30). Por eso el MAE de validación de los ensembles seleccionados es optimista y la medida honesta es el test.

**Test.** Confirma el primer puesto. El contraste diario (bloques móviles de siete días, 5000 réplicas; IC al 95 % de la diferencia rival menos ensemble) le da ventaja clara sobre la persistencia (+5,00 [3,93; 6,34]), LightGBM global (+2,44 [1,49; 3,46]) y GRU (+1,16 [0,66; 1,82]), con la semilla miembro (14,16 y 12,89), y la variante con ancla semanal (+0,44 [0,14; 0,78]). Frente a las medias simples no es significativa: +0,16 [−0,28; 0,76] con 12 familias y +0,31 [−0,05; 0,77] con 8.

**Aportación.** Se concentra en LightGBM global y la red densa: sin cada uno, el MAE de test sube a 12,70 y 12,18; los otros cuatro apenas cambian el test.

**Limitaciones.** La selección se prefiere por el criterio de validación, pero el método no se eligió a ciegas respecto al test: la regla anterior, media de las familias que batían a la persistencia en validación, incluía Ridge y SARIMAX, que no la baten en test (19,18 y 21,45 frente a 16,72), y se sustituyó tras verlo. El test no es del todo independiente para comparar selección y media simple.

Un menor MAE no garantiza mejor orden horario: la media de 12 familias acierta más el pico (78,3 % frente a 76,4 %), y el seq2seq de precio absoluto y Ridge llegan al 81,4 % y 81,6 %, sin contraste de incertidumbre.

Las previsiones de REE para D+1 se cargaron ya revisadas en validación y test, por lo que el MAE de test y la mejora del 29,9 % pueden ser optimistas frente a producción, que solo dispone de la previsión de las 11:00 de D. Queda pendiente medirlo reentrenando sin esas variables.
