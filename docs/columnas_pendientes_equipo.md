# Columnas pendientes de discusión con el equipo

Registro de las columnas que **el script sabe construir pero que NO entran al dataset de
modelado por defecto**, por decisión de la reunión del equipo del 20 de agosto de 2026: el
dataset se limita a las columnas explícitamente confirmadas en las 4 categorías acordadas
(FORECAST de `esios_forecast_da`, GENERACIÓN, DEMANDA E INTERCONEXIONES de `load_inter`,
COMMODITIES). Todo lo que no estaba en esa lista queda fuera por ahora, aquí documentado para
que el equipo lo revise y decida caso por caso.

**Cómo recuperarlas para explorar**, sin tocar el código: `construir_dataset_diario(incluir_columnas_pendientes=True)`.

---

## 1. Previsión oficial de ENTSO-E (`entsoe_forecast_da`)

**Columnas:** `load_forecast_mw`, `wind_forecast_mw`, `solar_forecast_mw`
**Qué es:** una segunda previsión día-adelante, independiente de ESIOS, publicada por el operador
europeo. Leak-safe (se publica antes del cierre del mercado).
**Por qué no está en el dataset del equipo:** no se nombró en la lista de columnas confirmada.
**Implicación de dejarla fuera:** probablemente baja. Es la misma magnitud física que ya cubren
`demanda_mercado_prev_mw`, `gen_wind_prev_mw` y `gen_solar_pv_prev_mw` de ESIOS — dos fuentes
prediciendo lo mismo van a estar muy correlacionadas entre sí, así que el dataset no pierde una
señal nueva, pierde una vista redundante de una señal que ya tiene.

## 2. Diferencia entre previsiones (ESIOS vs. ENTSO-E)

**Columnas derivadas:** `diff_demanda`, `diff_eolica`, `diff_solar`
**Qué es:** cuánto se separan las dos previsiones del punto 1 para el mismo día — un proxy de
"cuán incierto es mañana" (cuando dos fuentes independientes discrepan mucho, suele ser una señal
de que el día es más difícil de prever).
**Por qué no está:** depende directamente de la previsión de ENTSO-E del punto 1, que tampoco
entró.
**Implicación de dejarla fuera:** esta es la más específica de perder, porque no hay ninguna otra
columna en el dataset que mida "incertidumbre del día" — es un tipo de señal distinto, no
redundante con nada más. El efecto sobre el error puntual (MAE) probablemente sea modesto, pero
sería más relevante si en algún momento se construye la capa de incertidumbre de la arquitectura
(intervalos de predicción, no solo el valor puntual).

## 3. Capacidad disponible (`esios_capacity_available`)

**Columna:** `capacidad_disp_total_mw` (potencia total disponible del día, tras descontar
indisponibilidades declaradas)
**Qué es:** ver decisión D-01 en `docs/decisiones_datos.md` — de las variables que mejor explican
los picos de precio, porque capta paradas de nucleares y mantenimientos (una nuclear en recarga
quita ~1 GW del sistema).
**Por qué no está:** no se nombró en la lista de columnas confirmada.
**Implicación de dejarla fuera:** **probablemente la más significativa de las cuatro.** Es la
única columna del dataset que anticipa restricciones de oferta *antes* de que ocurran, y ya
estaba señalada como una de las variables más potentes del catálogo antes de esta reunión. Vale
la pena que el equipo la revise con cuidado en la próxima conversación — no es una columna
más, es una de las que más peso se esperaba que tuviera.

## 4. Precio de países vecinos (`spot_price`)

**Columnas, con lag D-1/D-7:** `pt_entsoe` (Portugal), `fr_entsoe` (Francia)
**Qué es:** precio real de mercado de los dos únicos países con interconexión física a España.
Portugal correlaciona **0,997** con el precio español (prácticamente el mismo mercado, MIBEL);
Francia **0,70**.
**Por qué no está:** no se nombró en la lista de columnas confirmada (viene de `spot_price`, no
de ninguna de las 4 tablas discutidas).
**Implicación de dejarla fuera:** **también significativa**, en particular Portugal — una
correlación de 0,997 es casi tan fuerte como el lag del propio precio español, así que se está
dejando fuera una de las señales más fuertes que se habían encontrado en todo el proyecto. Francia
aporta menos por sí sola, pero confirma la dinámica de acoplamiento del mercado ibérico con el
resto de Europa.

## 5. `demanda_residual_prev_mw` (`esios_forecast_da`)

**Motivo de exclusión distinto a los cuatro anteriores — no es una decisión de alcance, es una
fuga de información:** este indicador se revisa 10-14 días después de publicarse (verificado con
`check_tables/verificar_revision_indicadores.py`). El valor guardado hoy en la base para una
fecha pasada es el ya revisado, no el que existía en el momento de predecir — misma familia de
bug que el del target D+1 corregido el 17-ago-2026.
**Implicación de dejarla fuera:** ninguna pérdida real — nunca fue una columna válida para
entrenar tal cual estaba en la base, así que no se está sacrificando señal, se está evitando un
error de metodología.

## 6. Tres columnas de calendario/régimen encontradas en el trabajo independiente de un compañero

**Origen:** revisión de `ingesta/dt_maestro_sergio/` (25-ago-2026) — ver nota 23 de
`docs/notas_memoria_tfm.md`. No están construidas en nuestro `construir_dataset_maestro.py` /
`construir_dataset_horario.py`; se documentan aquí para decidir si se incorporan.

**Columnas:**
- `d1_es_puente` — indicador de "día puente" (día laborable entre un festivo y un fin de semana).
- `d1_vispera_festivo` — indicador de que el día siguiente es festivo.
- `d1_regimen_tope_gas` — indicador de si la fecha cae dentro del período en que estuvo vigente el
  mecanismo ibérico de tope al precio del gas para generación eléctrica (15-jun-2022 a
  31-dic-2023, según su registro).

**Qué son:** las dos primeras son variantes baratas del calendario que ya usamos (festivos,
fin de semana) — capturan patrones de demanda atípicos alrededor de un festivo que el indicador
binario simple de "festivo sí/no" no distingue. La tercera marca un cambio estructural real de las
reglas del mercado español (el mecanismo ibérico cambió cómo se forma el precio durante ese
período), algo que el modelo no puede inferir de otras columnas.

**Por qué no están:** no se habían identificado hasta esta revisión; no es una decisión de
exclusión, es un descubrimiento pendiente de evaluar.

**Implicación de incorporarlas:** bajo costo de implementación (son derivadas del calendario y de
dos fechas fijas conocidas), y el indicador de tope al gas en particular podría ayudar
específicamente con el problema de eventos extremos/2022 que ya estamos investigando en la capa de
incertidumbre — sería razonable probarlo primero ahí.

## 7. NTC Marruecos (`ree_ntc_impma`, `ree_ntc_expma`, `ntc_ma_imp_prev_mw`, `ntc_ma_exp_prev_mw`) — duda de origen sin resolver

**El problema, con números:** estas cuatro columnas combinan dos síntomas a la vez, algo que no
pasa con ninguna otra columna del dataset horario: `ntc_ma_imp_prev_mw`/`ntc_ma_exp_prev_mw`
tienen 16,76% de nulos (7.351 horas) **y**, sobre lo que sí tiene dato, 9,4-9,7% de valores atípicos
(IQR×3); `ree_ntc_impma` no tiene nulos pero sí 13,29% de atípicos — la tasa de atípicos más alta
de todo el dataset después de `coal_antracita_mw` (que ya se investigó y se confirmó que es un
cambio de régimen real, no suciedad — ver nota correspondiente).

**Por qué no se limpia todavía:** limpiar (imputar, capar atípicos) sin saber si la fuente es
fiable sería resolver el síntoma sin saber si hay una enfermedad debajo. La interconexión con
Marruecos es la más pequeña y la más nueva de las tres (Francia, Portugal, Marruecos), y es
razonable que tenga un historial de datos distinto — pero **no está confirmado con el equipo** si
esos nulos/atípicos combinados son una particularidad legítima de esa interconexión o un problema
de la fuente de datos.

**Qué hace falta antes de escribir ninguna línea de código de limpieza:** que el equipo confirme
el origen y la fiabilidad de estas cuatro columnas — igual que se hizo con `coal_antracita_mw`,
antes de decidir cómo tratarlas hay que saber qué son.

---

## En una frase para la reunión

De las primeras cinco, las números **3 (capacidad disponible)** y **4 (precio de
Portugal/Francia)** eran las que con más evidencia previa apuntaban a tener peso real en el
modelo — ya incorporadas al dataset horario. Los puntos 1 y 2 son de impacto esperado
bajo-moderado. El punto 5 no es una pérdida, es una fuga evitada. El punto **6** son tres columnas
de calendario baratas de añadir. El punto **7 (NTC Marruecos) es el que más urge resolver con el
equipo**: es la única combinación de nulos altos + atípicos altos del dataset cuyo origen no está
confirmado.
