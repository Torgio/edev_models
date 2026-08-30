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
