-- =====================================================================================
--  Casos de estudio de baterias  ·  TFM Energia UCM
--
--  psql "$DATABASE_URL" -f sql/crear_tablas_bess.sql
-- =====================================================================================
--
--  TODO CUELGA DE UN USUARIO. La ficha de bateria, las instalaciones y los casos son
--  suyos, y sus codigos son unicos DENTRO de su cuenta -- dos personas pueden llamar
--  'LFP-4H' a fichas distintas sin pisarse.
--
--  CINCO NIVELES, CINCO VIDAS DISTINTAS
--
--    app_user        quien hace el estudio.
--
--    app_battery_model
--                            la FICHA de la bateria, no el caso. La distincion importa: un caso es
--                        UN estudio concreto, y la ficha es lo que se repite -- el mismo usuario
--                        prueba la misma bateria en varios periodos, en standalone y en
--                        autoconsumo, o contra dos instalaciones. Si la ficha viviera dentro del
--                        caso, cada estudio guardaria su copia, y bastaria que uno tuviera 6.000
--                        ciclos y otro 6.500 -- por un dedazo -- para que la comparacion entre
--                        ellos dejara de significar nada sin que nada avisara.
--
--    app_consump_inst    la instalacion de CONSUMO, con su curva.
--    app_gen_inst        la instalacion de GENERACION, con la suya.
--                        Van separadas, y no como un solo "emplazamiento", porque son
--                        independientes: se puede estudiar un consumo SIN generacion -- la
--                        bateria arbitrando la tarifa por detras del contador, que es un caso
--                        real y frecuente -- o combinar un mismo consumo con dos dimensionados
--                        de solar para ver cual conviene.
--
--    app_study_case
--                            el CASO: usuario + bateria + consumo? + generacion? + periodo + reglas.
--                        Es la unidad que se nombra y se cita.
--
--    app_case_run
--                            cada EJECUCION del caso. El mismo caso se reejecuta cada vez que la
--                        curva se republica, y hay que poder comparar la de hoy con la de hace
--                        un mes sin duplicar la definicion.
--
--  EL PERIODO PUEDE EMPEZAR EN EL PASADO, Y ESO CAMBIA EL PRECIO
--
--  Segun el dia, el precio viene de tres sitios:
--      pasado        `spot_price`      el PMD publicado. UNA sola realizacion.
--      ya predicho   `predictions`     el ensemble. Una sola realizacion.
--      futuro        la curva          N escenarios.
--
--  De ahi dos cosas que estan en el esquema a proposito:
--
--    1. `app_case_run.split_date` marca la frontera. Un caso que empieza en el pasado es un
--       BACKTEST -- "cuanto habria ganado esta bateria en 2024" -- y esa es una pregunta
--       distinta, con la ventaja de que el precio no se lo invento nadie.
--
--    2. En el tramo pasado NO hay percentiles: hay una unica realizacion. Por eso los
--       percentiles de `app_case_result_annual` van a NULL en los años anteriores a la
--       frontera. Dibujar una banda P10-P90 sobre dias cuyo precio ya se conoce seria
--       inventar una incertidumbre que no existe.
--
--  QUE NO ESTA AQUI, Y POR QUE
--  Los escenarios de precio -- 50 x 175.320 = 8,8 millones de flotantes, 33 MB -- viven en
--  el `.npy` que publica `generar_curva.py`. `app_curve.artifact_path` apunta a el. En
--  Postgres solo los percentiles, que es lo que se consulta.
-- =====================================================================================


-- retirada de la forma anterior -----------------------------------------------------
-- Solo si se detecta el esquema viejo, para que reejecutar esto no borre la curva publicada.
-- La vista lleva un `LEFT JOIN LATERAL (SELECT * FROM app_case_run)`, y ese `SELECT *` la
-- hace depender de TODAS las columnas: mientras exista, cualquier DROP COLUMN falla. Se
-- retira aqui y se vuelve a crear al final del fichero, ya con el esquema nuevo.
DROP VIEW IF EXISTS app_case_summary;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'app_curve_hourly' AND column_name = 'curve_id') THEN
        DROP TABLE IF EXISTS app_curve_hourly;
        DROP TABLE IF EXISTS app_curve;
        RAISE NOTICE 'retirado el esquema con curve_id; republica la curva';
    END IF;
END $$;


-- 0 - el usuario ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_user (
    user_id             SERIAL PRIMARY KEY,
    email               TEXT UNIQUE NOT NULL,
    name                TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- 1 - la ficha de bateria ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_battery_model (
    battery_id          SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES app_user ON DELETE CASCADE,
    code                TEXT NOT NULL,               -- 'LFP-4H-1MW'
    name                TEXT NOT NULL,
    -- Todo va referido a la potencia NOMINAL, que es como vienen las fichas comerciales.
    power_mw            REAL NOT NULL,
    duration_h          REAL NOT NULL,               -- energia = power_mw * duration_h
    charge_max_pct      REAL NOT NULL DEFAULT 100,   -- % de la nominal
    discharge_max_pct   REAL NOT NULL DEFAULT 100,
    -- Minimo tecnico del inversor. No es una cota: es "o cero o al menos esto", asi que
    -- necesita una binaria por hora y convierte el problema de LP en MILP. Medido sobre 90
    -- dias: cuesta 0,04 % de margen y multiplica el tiempo por 20-40. Por eso el defecto es
    -- cero -- se enciende cuando hace falta un perfil fisicamente realizable, no para
    -- calcular el margen.
    power_min_pct       REAL NOT NULL DEFAULT 0,
    efficiency_rt       REAL NOT NULL DEFAULT 0.90,
    soc_min             REAL NOT NULL DEFAULT 0.05,
    soc_max             REAL NOT NULL DEFAULT 0.95,
    cycle_life          INTEGER NOT NULL DEFAULT 6000,
    degradation_per_1000 REAL NOT NULL DEFAULT 3.0,  -- % de capacidad por 1.000 ciclos
    degradation_annual  REAL NOT NULL DEFAULT 1.5,   -- % al año, aunque no se use
    capex_eur_mwh       REAL NOT NULL DEFAULT 200000,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT battery_code_unico UNIQUE (user_id, code),
    -- La ficha tiene que ser coherente consigo misma: los ciclos de vida por la degradacion
    -- por ciclo deben dar cerca del 20 %, que es como se define el fin de vida (80 % de
    -- capacidad restante). 6.000 x 3 %/1000 = 18 %: entra. 10.000 x 5 %/1000 = 50 %: no.
    CONSTRAINT ficha_coherente CHECK (
        cycle_life * degradation_per_1000 / 1000.0 BETWEEN 10 AND 35),
    CONSTRAINT soc_ordenado CHECK (soc_min < soc_max),
    CONSTRAINT minimo_por_debajo_del_maximo CHECK (power_min_pct < charge_max_pct)
);


-- 2 - la instalacion de CONSUMO ------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_consump_inst (
    consump_id          SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES app_user ON DELETE CASCADE,
    code                TEXT NOT NULL,
    name                TEXT NOT NULL,
    annual_mwh          REAL NOT NULL,
    growth_pct          REAL NOT NULL DEFAULT 1.0,   -- crecimiento anual del consumo
    -- LA DIFERENCIA ENTRE ESTOS DOS ES EL NEGOCIO DEL AUTOCONSUMO. Con recargo 0 y
    -- compensacion al 100 %, importar y exportar cuestan lo mismo, la instalacion es neutra
    -- y el valor de la bateria se reduce EXACTAMENTE al arbitraje puro del modo standalone.
    -- Comprobado: los dos modos daban la misma cifra hasta poner valores realistas.
    tariff_markup_eur_mwh REAL NOT NULL DEFAULT 70,  -- peajes y cargos al IMPORTAR
    export_price_pct    REAL NOT NULL DEFAULT 80,    -- % del spot al EXPORTAR
    contracted_power_mw REAL,                        -- tope de importacion, si lo hay
    source_file         TEXT,                        -- de que Excel salio la curva
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT consump_code_unico UNIQUE (user_id, code)
);


-- 3 - la instalacion de GENERACION ---------------------------------------------------
CREATE TABLE IF NOT EXISTS app_gen_inst (
    gen_id              SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES app_user ON DELETE CASCADE,
    code                TEXT NOT NULL,
    name                TEXT NOT NULL,
    technology          TEXT NOT NULL DEFAULT 'fv'
                        CHECK (technology IN ('fv', 'eolica', 'otra')),
    capacity_mwp        REAL NOT NULL,
    degradation_pct     REAL NOT NULL DEFAULT 0.5,   -- perdida anual de rendimiento
    export_limit_mw     REAL,                        -- tope de vertido a red, si lo hay
    source_file         TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT gen_code_unico UNIQUE (user_id, code)
);


-- la FORMA de cada curva, normalizada a media 1 --------------------------------------
-- El tamaño va en la cabecera (annual_mwh, capacity_mwp) y aqui solo la forma. Asi el
-- mismo perfil sirve para un cliente de 200 MWh/año y para uno de 2.000, y proyectar a
-- veinte años es multiplicar en vez de volver a pedir el Excel.
--
-- Se indexa por (mes, tipo de dia, hora) y NO por dia del año, por dos motivos: el dia del
-- año no casa entre bisiestos, y un lunes de julio no se parece a un domingo de julio.
-- 12 x 2 x 24 = 576 filas por instalacion.
CREATE TABLE IF NOT EXISTS app_consump_shape (
    consump_id          INTEGER NOT NULL REFERENCES app_consump_inst ON DELETE CASCADE,
    month               SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day_type            TEXT NOT NULL CHECK (day_type IN ('laborable', 'finde')),
    hour                SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
    value_pu            REAL NOT NULL,
    PRIMARY KEY (consump_id, month, day_type, hour)
);

CREATE TABLE IF NOT EXISTS app_gen_shape (
    gen_id              INTEGER NOT NULL REFERENCES app_gen_inst ON DELETE CASCADE,
    month               SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day_type            TEXT NOT NULL CHECK (day_type IN ('laborable', 'finde')),
    hour                SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),
    value_pu            REAL NOT NULL,
    PRIMARY KEY (gen_id, month, day_type, hour)
);


-- 4 - la curva publicada -------------------------------------------------------------
-- UNA SOLA CURVA, que se pisa en cada publicacion. No hay historico y no hay `curve_id`:
-- con una unica fila, un identificador no distingue nada.
--
-- Son 178.224 filas por curva. Guardando una al dia serian 65 millones de filas y 2,9 GB al
-- año, y no aportaria: las anclas del escenario son medias del año en curso y entre dos dias
-- no se mueven. La curva es una VISTA ACTUAL, no un archivo.
--
-- El indice unico sobre una expresion constante es lo que impide que se cuele una segunda
-- fila: asi la invariante la garantiza la base y no la buena voluntad del proximo script.
CREATE TABLE IF NOT EXISTS app_curve (
    generated_at        TIMESTAMPTZ NOT NULL,
    last_observed_date  DATE NOT NULL,      -- hasta donde llegaba el dato real
    date_from           DATE NOT NULL,
    date_to             DATE NOT NULL,
    n_scenarios         INTEGER NOT NULL,
    n_hours             INTEGER NOT NULL,
    engine              TEXT NOT NULL DEFAULT 'fundamental',
    matrix_name         TEXT,               -- 'produccion' o 'nucleo'
    matrix_hash         TEXT,
    -- Los escenarios -- 50 x 178.224 = 8,9 millones de flotantes, 34 MB -- viven en este
    -- `.npy`. En Postgres solo los percentiles, que es lo que se consulta.
    artifact_path       TEXT NOT NULL,
    gas_scenario        JSONB,
    demand_scenario     JSONB,
    solar_scenario      JSONB,
    wind_scenario       JSONB
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_curve_unica ON app_curve ((TRUE));


-- EL EJE TEMPORAL NO SON INSTANTES REALES.
-- `curva_fundamental` genera una rejilla NOMINAL de 24 horas por dia: el cambio de hora ya
-- se resolvio al construir la matriz (la hora repetida de octubre se promedia, la que falta
-- en marzo se interpola), asi que en la curva marzo y octubre tienen 24 horas como todos.
--
-- Con TIMESTAMPTZ esto rompe: el 28-03-2027 las 02:00 no existe en Madrid, y Postgres
-- convierte las 02:00 Y las 03:00 en la misma marca. Aqui reventaba la clave primaria, que
-- al menos se ve. `spot_price` y `predictions` si son instantes reales y llevan TIMESTAMPTZ.
CREATE TABLE IF NOT EXISTS app_curve_hourly (
    datetime            TIMESTAMP PRIMARY KEY,
    p10 REAL, p50 REAL, p90 REAL
);


-- 5 - el caso de estudio -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_study_case (
    case_id             SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES app_user ON DELETE CASCADE,
    code                TEXT NOT NULL,               -- 'BESS-4H-VALLADOLID'
    name                TEXT NOT NULL,
    mode                TEXT NOT NULL CHECK (mode IN ('standalone', 'autoconsumo')),
    battery_id          INTEGER NOT NULL REFERENCES app_battery_model,
    -- Las dos por separado y las dos opcionales: se puede estudiar un consumo SIN
    -- generacion -- la bateria arbitrando la tarifa detras del contador -- y tambien una
    -- generacion sin consumo, que es una planta con almacenamiento vertiendo a red.
    consump_id          INTEGER REFERENCES app_consump_inst,
    gen_id              INTEGER REFERENCES app_gen_inst,
    date_from           DATE NOT NULL,               -- puede estar en el PASADO
    date_to             DATE NOT NULL,
    charge_policy       TEXT NOT NULL DEFAULT 'libre'
                        CHECK (charge_policy IN ('libre', 'prefiere_excedente',
                                                 'solo_excedente')),
    window_days         SMALLINT NOT NULL DEFAULT 7, -- entre cierres del estado de carga
    cycle_cost_eur_mwh  REAL,                        -- NULL = se calcula de la ficha
    discount_rate       REAL NOT NULL DEFAULT 0.07,
    opex_pct            REAL NOT NULL DEFAULT 0.015,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT case_code_unico UNIQUE (user_id, code),
    CONSTRAINT periodo_ordenado CHECK (date_from < date_to),
    -- Un autoconsumo sin consumo NI generacion no tiene nada que optimizar detras del
    -- contador: seria un standalone con otro nombre.
    CONSTRAINT autoconsumo_necesita_instalacion CHECK (
        mode <> 'autoconsumo' OR consump_id IS NOT NULL OR gen_id IS NOT NULL),
    -- Y un standalone que las lleve tiene el modo mal puesto.
    CONSTRAINT standalone_sin_instalacion CHECK (
        mode <> 'standalone' OR (consump_id IS NULL AND gen_id IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_study_case_user ON app_study_case (user_id, created_at DESC);


-- 6 - cada ejecucion -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_case_run (
    run_id              SERIAL PRIMARY KEY,
    case_id             INTEGER NOT NULL REFERENCES app_study_case ON DELETE CASCADE,
    -- La curva se pisa, asi que una clave foranea a su fila no da trazabilidad: apuntaria a
    -- un contenido que ya es otro. Se copian AL EJECUTAR los dos datos que importan. Es una
    -- foto, y una foto no se pisa.
    curve_generated_at  TIMESTAMPTZ,
    curve_matrix_hash   TEXT,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- LA FRONTERA. Antes de esta fecha el precio es real y hay UNA sola realizacion; a
    -- partir de ella es simulado y hay `n_scenarios`. Sin esto se acaba publicando una
    -- banda P10-P90 sobre dias cuyo precio ya se conocia.
    split_date          DATE,
    days_historical     INTEGER NOT NULL DEFAULT 0,
    days_simulated      INTEGER NOT NULL DEFAULT 0,
    n_scenarios         INTEGER NOT NULL,
    solver              TEXT NOT NULL DEFAULT 'highs-lp',  -- 'highs-milp' con minimo tecnico
    solver_seconds      REAL,
    margin_total_mean   REAL,        -- EUR en todo el periodo
    margin_annual_mean  REAL,        -- EUR/año por MW instalado
    savings_vs_no_batt  REAL,        -- solo autoconsumo: ahorro frente a no tener bateria
    cycles_per_day      REAL,
    life_years          REAL,
    npv_p10 REAL, npv_p50 REAL, npv_p90 REAL,
    npv_positive_pct    REAL,
    capex_coverage_pct  REAL,
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS ix_case_run_caso ON app_case_run (case_id, run_at DESC);


-- 7 - resultados por año -------------------------------------------------------------
-- Los percentiles van a NULL en los años anteriores a `split_date`: ahi el precio es el que
-- fue, hay una sola realizacion, y una banda seria inventada.
CREATE TABLE IF NOT EXISTS app_case_result_annual (
    run_id              INTEGER NOT NULL REFERENCES app_case_run ON DELETE CASCADE,
    year                SMALLINT NOT NULL,
    origin              TEXT NOT NULL CHECK (origin IN ('historico', 'modelo', 'simulado')),
    days                SMALLINT NOT NULL,
    margin_mean         REAL NOT NULL,
    p5 REAL, p10 REAL, p25 REAL, p50 REAL, p75 REAL, p90 REAL, p95 REAL,
    cycles_per_day      REAL,
    energy_charged_mwh  REAL,
    energy_discharged_mwh REAL,
    grid_import_mwh     REAL,
    grid_export_mwh     REAL,
    PRIMARY KEY (run_id, year)
);


-- 8 - el despacho horario ------------------------------------------------------------
-- Solo para los escenarios que se pidan -- normalmente el P10, el P50 y el P90 -- y no para
-- los 50: son 526.000 filas por ejecucion en vez de 8,8 millones. Es lo que alimenta las
-- graficas del mes.
CREATE TABLE IF NOT EXISTS app_case_dispatch (
    run_id              INTEGER NOT NULL REFERENCES app_case_run ON DELETE CASCADE,
    scenario            SMALLINT NOT NULL,      -- 0 en el tramo historico
    -- SIN ZONA, igual que app_curve_hourly: el despacho vive en la misma rejilla nominal de
    -- 24 h/dia que la curva. Con TIMESTAMPTZ, el domingo de marzo las 02:00 y las 03:00
    -- colapsan en la misma marca y chocan contra la clave primaria. Aqui ademas era peor que
    -- un error: el INSERT llevaba ON CONFLICT DO NOTHING y perdia esas horas en silencio.
    datetime            TIMESTAMP NOT NULL,
    price               REAL NOT NULL,
    charge_mw           REAL NOT NULL,
    discharge_mw        REAL NOT NULL,
    soc_mwh             REAL NOT NULL,
    grid_import_mwh     REAL NOT NULL DEFAULT 0,
    grid_export_mwh     REAL NOT NULL DEFAULT 0,
    load_mwh            REAL NOT NULL DEFAULT 0,
    generation_mwh      REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, scenario, datetime)
);


-- una vista, para no repetir el JOIN de seis tablas en cada consulta ------------------



-- migraciones idempotentes -----------------------------------------------------------
-- `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe, asi que los cambios de
-- esquema posteriores van aqui. Reejecutar el fichero entero los aplica.

-- Se retira `curve_id`. Con una sola curva no distinguia nada, y en `app_case_run` era peor
-- que inutil: apuntaba a una fila que se pisa, dando apariencia de trazabilidad sin darla.
ALTER TABLE app_case_run DROP COLUMN IF EXISTS curve_id;
ALTER TABLE app_case_run ADD COLUMN IF NOT EXISTS curve_generated_at TIMESTAMPTZ;
ALTER TABLE app_case_run ADD COLUMN IF NOT EXISTS curve_matrix_hash TEXT;

-- El eje de la curva es una rejilla nominal, no instantes: ver la nota de app_curve_hourly.
ALTER TABLE app_case_dispatch ALTER COLUMN datetime TYPE timestamp;


-- la vista va la ULTIMA: depende de las columnas que las migraciones acaban
-- de cambiar.
CREATE OR REPLACE VIEW app_case_summary AS
SELECT u.email, c.code, c.name, c.mode, c.date_from, c.date_to,
       b.code AS battery, b.power_mw, b.duration_h,
       cs.code AS consumo, cs.annual_mwh,
       g.code AS generacion, g.capacity_mwp,
       r.run_id, r.run_at, r.split_date, r.days_historical, r.days_simulated,
       r.margin_annual_mean, r.cycles_per_day, r.npv_p50, r.npv_positive_pct,
       r.curve_generated_at, r.curve_matrix_hash
FROM app_study_case c
JOIN app_user u USING (user_id)
JOIN app_battery_model b USING (battery_id)
LEFT JOIN app_consump_inst cs USING (consump_id)
LEFT JOIN app_gen_inst g USING (gen_id)
LEFT JOIN LATERAL (
    SELECT * FROM app_case_run WHERE case_id = c.case_id ORDER BY run_at DESC LIMIT 1
) r ON TRUE;
