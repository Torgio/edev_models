## Propuesta de figuras (máximo dos)

### Figuras que se proponen

**1. Figura X: `fig_permutacion_ensemble.png`.** Es la única explicación del sistema que se sube a producción, no de un miembro.

*Pie:* Aumento del MAE de test del ensemble seleccionado (base 11,72 €/MWh; 212 días de enero a julio de 2026) al permutar entre días cada grupo de variables, a la vez en todos los formatos de entrada. Cada día recibe del día donante sus 24 horas y, en las redes, su ventana de 168 h. La barra es la media de tres permutaciones aleatorias de los días sobre el mismo modelo, y los bigotes marcan su mínimo y su máximo. El grupo del precio español reúne D, D-1 y D-6; el precio portugués y los diferenciales están en el de precios europeos. El ancla del residuo se mantiene fija; la barra rayada y cortada muestra, como referencia, el efecto de permutar también el ancla (+20,36 €/MWh).

*Datos:* `permutacion_ensemble.csv` (por miembro, `permutacion_ensemble_miembros.csv`).

**2. Figura Y: `fig_comportamiento.png`.** Resume en una imagen la cercanía a la forma de D, el error según cuánto cambia el día y lo que ve un operador.

*Pie:* (a) MAE de test por cuartil del cambio real medio diario |D+1 − D| (53 días por cuartil) para la persistencia (gris) y el ensemble seleccionado (azul); por construcción, el MAE de la persistencia en cada cuartil es el cambio medio del cuartil. (b) Lunes 23/03/2026: precio de D (gris discontinuo), real (negro) y ensemble (azul). Es el día del cuartil de mayor cambio cuyo cociente entre el error del ensemble y el de la persistencia es la mediana del cuartil (MAE 12,80 frente a 25,24 €/MWh). Ese día el ensemble sobrestima la madrugada y se queda corto en el pico de la tarde.

*Datos:* `comportamiento.json` y `comportamiento.csv`.

### Figuras que se descartan

- **`fig_decision.png`.** Repite la Tabla X, con los mismos MAE de validación y test. Con tantas etiquetas y la nota de valores fuera de escala necesita el ancho completo de la página. Solo tendría sentido si se quitara la tabla.
- **`fig_shap_lgbm.png`.** Tiene tres problemas:
  - Explica un solo miembro (11/30), y su mensaje por grupos cabe en dos frases del texto.
  - El panel b destaca la separación entre el precio portugués y el español, que es justo lo que no se puede interpretar, y muestra nombres de columna del código.
  - Meteorología y capacidades comparten el mismo gris.

### Ajustes si se regenera la Figura X

- **Unidades:** el eje dice "EUR/MWh"; conviene "€/MWh", como en la Figura Y y en el texto.
- **Grupo de precios europeos:** la etiqueta "Precios europeos y spreads (D)" debería decir "diferenciales" y hacer visible que incluye el precio portugués, por ejemplo "Precios europeos, portugués y diferenciales (D)".
- **Porcentajes:** los rótulos no llevan decimales (+86 %) y el texto usa uno (+86,2 %); conviene unificarlos.
- **Capacidades:** el rótulo "0,00 (0 %)" puede sustituirse por "≈ 0" para no sugerir un valor exacto (media −0,003; rango −0,06 a +0,04).
