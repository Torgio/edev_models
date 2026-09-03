-- ===========================================================================
-- Catalogo de modelos, metricas y BESS -- TFM prediccion de precio + baterias
--
-- LO QUE ESTA TABLA *NO* HACE
-- Las predicciones NO viven aqui: viven en `predictions`, que crea y llena
-- scripts/guardar_predicciones.py. Esa es la tabla del equipo y es la buena --
-- resuelve el cambio de hora con timestamptz y distingue backfill de produccion
-- con la columna `source`. Aqui solo esta lo que le falta: quien es cada modelo,
-- que columnas usa, y como de bien lo hace.
--
-- Las claves usan `model` y `seed`, los mismos nombres que `predictions`, para
-- que el JOIN sea directo y nadie tenga que acordarse de dos convenciones.
--
--   python modelos/crear_tablas_ml.py
-- ===========================================================================

-- ---------------------------------------------------------------- catalogo --
CREATE TABLE IF NOT EXISTS models (
    model            TEXT        NOT NULL,   -- igual que predictions.model
    seed             SMALLINT    NOT NULL DEFAULT -1,  -- -1 = sin semilla (ensemble, baselines)
    familia          TEXT        NOT NULL,   -- baseline|boosting|rnn|estadistico|ensemble
    autor            TEXT        NOT NULL,
    estado           TEXT        NOT NULL DEFAULT 'retador',  -- campeon|retador|retirado
    matrix           TEXT,
    matrix_hash      TEXT,                   -- debe casar con matriz_*.meta.json
    artefacto        TEXT,
    libreria         TEXT,                   -- con la version EXACTA
    python           TEXT,
    commit_sha       TEXT,
    entrenado_desde  DATE,                   -- ¿incluye la crisis de 2021-22?
    entrenado_hasta  DATE,                   -- control de fuga
    features         JSONB,
    features_dudosas JSONB NOT NULL DEFAULT '[]'::jsonb,
    registrado_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    notas            TEXT,
    CONSTRAINT pk_models PRIMARY KEY (model, seed),
    CONSTRAINT ck_models_estado CHECK (estado IN ('campeon','retador','retirado'))
);
COMMENT ON COLUMN models.entrenado_desde IS
    'No es decorativo: sin la crisis 2021-22 en la ventana, el modelo pierde contra la persistencia.';
COMMENT ON COLUMN models.features_dudosas IS
    'Columnas que el autor no tiene claro que esten publicadas antes de las 11:00 de D.';

-- ---------------------------------------------------------------- metricas --
CREATE TABLE IF NOT EXISTS model_metrics (
    model          TEXT NOT NULL,
    seed           SMALLINT NOT NULL DEFAULT -1,
    periodo        TEXT NOT NULL,                  -- 'val_2025'|'test_2026'|'prod_30d'
    corte          TEXT NOT NULL DEFAULT 'global', -- 'global'|'hora_18'|'finde'|'precio_cero'
    n_obs          INTEGER,
    mae            DOUBLE PRECISION,
    rmse           DOUBLE PRECISION,
    smape          DOUBLE PRECISION,               -- sMAPE y no MAPE: el spot toca cero
    pinball80      DOUBLE PRECISION,
    cobertura_ic80 DOUBLE PRECISION,
    captura_pct    DOUBLE PRECISION,
    eur_dia        DOUBLE PRECISION,
    pico_1h_pct    DOUBLE PRECISION,
    skill_vs_naive DOUBLE PRECISION,
    simulador      JSONB,                          -- supuestos de bateria de ESTE numero
    calculado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_model_metrics PRIMARY KEY (model, seed, periodo, corte)
);
COMMENT ON COLUMN model_metrics.simulador IS
    'La captura NO es una metrica unica: depende de potencia, duracion, eficiencia y ciclos. '
    'Se guarda junto al numero para que no vuelvan a compararse dos capturas incomparables.';

-- ------------------------------------------------------------ plan de BESS --
CREATE TABLE IF NOT EXISTS bess_plan (
    datetime    TIMESTAMPTZ      NOT NULL,   -- misma convencion que predictions
    model       TEXT             NOT NULL,
    carga_mw    DOUBLE PRECISION NOT NULL DEFAULT 0,
    descarga_mw DOUBLE PRECISION NOT NULL DEFAULT 0,
    soc_mwh     DOUBLE PRECISION,
    ingreso_eur DOUBLE PRECISION,
    simulador   JSONB,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_bess_plan PRIMARY KEY (datetime, model)
);

-- ------------------------------------------------- resultado real ex-post --
CREATE TABLE IF NOT EXISTS bess_result (
    fecha_objetivo      DATE             NOT NULL,
    model               TEXT             NOT NULL,
    ingreso_eur         DOUBLE PRECISION,  -- lo que gano la bateria con esta prediccion
    ingreso_oraculo_eur DOUBLE PRECISION,  -- techo: precio real conocido de antemano
    ingreso_naive_eur   DOUBLE PRECISION,  -- suelo: el precio de ayer
    captura_pct         DOUBLE PRECISION,
    ciclos              DOUBLE PRECISION,
    simulador           JSONB,
    calculado_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_bess_result PRIMARY KEY (fecha_objetivo, model)
);


-- ------------------------------------------------- la serie diaria del error --
-- `model_metrics` guarda ventanas (`prod_30d` se recalcula entera cada pasada y
-- borra el ayer). Esta tabla guarda el DIA, que es lo unico que permite dibujar si
-- la ventaja sobre la persistencia se mantiene o se apaga.
--
-- Se guardan los DOS MAE, no solo el skill: el skill de una ventana NO es la media
-- de los skills diarios. Un dia en que la persistencia acierta casi sola (naive de
-- 5,56 EUR/MWh el 30-ago-2026) da un skill de -233 % que arrastra cualquier media.
-- La ventana correcta es  1 - sum(mae*n_obs) / sum(mae_naive*n_obs),  y eso exige
-- los dos numeradores guardados.
CREATE TABLE IF NOT EXISTS model_metrics_daily (
    fecha          DATE     NOT NULL,          -- dia objetivo, hora peninsular
    model          TEXT     NOT NULL,          -- igual que predictions.model
    seed           SMALLINT NOT NULL DEFAULT -1,
    source         TEXT     NOT NULL DEFAULT 'production',  -- NO omitir: bess_result
                                               -- no lo tiene y por eso mezcla tramos
    n_obs          SMALLINT,                   -- horas con prediccion Y precio real
    mae            DOUBLE PRECISION,
    mae_naive      DOUBLE PRECISION,           -- la persistencia sobre LAS MISMAS horas
    skill_vs_naive DOUBLE PRECISION,           -- 100*(1 - mae/mae_naive) DE ESE DIA
    estado         TEXT     NOT NULL DEFAULT 'ok',   -- ok|cambio_hora|horas_incompletas|sin_naive
    naive_regla    TEXT,                       -- que se entiende por "el precio de ayer"
    calculado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_model_metrics_daily PRIMARY KEY (fecha, model, seed, source)
);
COMMENT ON COLUMN model_metrics_daily.naive_regla IS
    'Misma hora 24 h antes (desplazamiento absoluto) o misma hora local de D-1 no son lo '
    'mismo los dos domingos del cambio de hora. Se guarda la regla junto al numero.';
COMMENT ON COLUMN model_metrics_daily.estado IS
    'ok                = 24 horas comparables, dia normal. '
    'cambio_hora       = el dia tiene 23 o 25 horas segun el calendario (no segun cuantas '
                        'filas llegaron). NO invalida el error: el MAE es comparable. Lo '
                        'que no lo es es el dinero, que necesita 24 h para cerrar el ciclo. '
    'ayer_cambio_hora  = hoy es normal pero el naive sale de un dia que no lo era, asi que '
                        'la persistencia de alguna hora esta incompleta. Dos dias al año. '
    'naive_perfecto    = mae_naive = 0 (el precio no se movio de un dia para otro): el dia '
                        'es valido y cuenta, pero no hay skill que medir. NO es sin_naive. '
    'horas_incompletas = faltan horas en un dia que deberia tener 24: un hueco de datos. '
    'sin_naive         = no hay dia anterior. Es el unico estado que la vista excluye.';

CREATE INDEX IF NOT EXISTS ix_mmd_modelo_fecha
    ON model_metrics_daily (model, seed, source, fecha);

-- ------------------------------------------------------ la ventana movil, en SQL --
-- La media movil se calcula AQUI y no en el navegador: es una suma sobre filas ya
-- guardadas, no una metrica nueva. Cambiar 6 por 13 da la de 14 dias.
CREATE OR REPLACE VIEW model_metrics_daily_7d AS
SELECT fecha, model, seed, source, n_obs, mae, mae_naive, skill_vs_naive, estado,
       100 * (1 - SUM(mae * n_obs)       OVER w
                / NULLIF(SUM(mae_naive * n_obs) OVER w, 0))  AS skill_7d,
       count(*) OVER w                                        AS dias_en_ventana
  FROM model_metrics_daily
 -- Se filtra por el HECHO (¿hubo naive?), no por una lista de etiquetas: una lista
 -- hay que acordarse de ampliarla cada vez que nace un estado nuevo, y el dia que no se
 -- amplia deja dias buenos fuera de la media sin que nadie lo note. `n_obs` ya pondera:
 -- un dia con 23 horas pesa 23, no se descarta.
 WHERE mae_naive IS NOT NULL AND estado <> 'sin_naive'
WINDOW w AS (PARTITION BY model, seed, source ORDER BY fecha
             ROWS BETWEEN 6 PRECEDING AND CURRENT ROW);
