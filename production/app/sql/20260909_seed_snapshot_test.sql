-- Synthetic, non-personal fixtures for tfm_energia_test only.
-- The setup script verifies current_database() before running this file.
CREATE TABLE public.spot_price (
    datetime TIMESTAMPTZ PRIMARY KEY,
    es_esios DOUBLE PRECISION
);
CREATE TABLE public.predictions (
    datetime TIMESTAMPTZ NOT NULL,
    model TEXT NOT NULL,
    prediction DOUBLE PRECISION,
    PRIMARY KEY (datetime, model)
);

INSERT INTO public.spot_price (datetime, es_esios)
SELECT stamp, 60 + 25 * sin(extract(hour FROM stamp AT TIME ZONE 'Europe/Madrid') * pi() / 12)
FROM generate_series(
    '2026-01-05 00:00 Europe/Madrid'::timestamptz,
    '2026-01-06 23:00 Europe/Madrid'::timestamptz,
    interval '1 hour'
) AS stamp;

INSERT INTO public.app_user (email, name)
VALUES ('snapshot-test@local.invalid', 'Prueba aislada')
RETURNING user_id;

INSERT INTO public.app_battery_model (
    user_id, code, name, power_mw, duration_h, efficiency_rt, cycle_life,
    degradation_per_1000, degradation_annual, capex_eur_mwh
)
SELECT user_id, 'BAT-TEST-50', 'Batería sintética 50 kW / 4 h', .05, 4, .90,
       6000, 3, 1.5, 200000
FROM public.app_user WHERE email = 'snapshot-test@local.invalid';

INSERT INTO public.app_consump_inst (
    user_id, code, name, annual_mwh, growth_pct, tariff_markup_eur_mwh,
    export_price_pct, contracted_power_mw
)
SELECT user_id, 'CONSUMO-TEST', 'Consumo sintético', 350, 1, 70, 80, .10
FROM public.app_user WHERE email = 'snapshot-test@local.invalid';

INSERT INTO public.app_gen_inst (
    user_id, code, name, technology, capacity_mwp, degradation_pct, export_limit_mw
)
SELECT user_id, 'FV-TEST', 'Solar sintética', 'fv', .25, .5, .20
FROM public.app_user WHERE email = 'snapshot-test@local.invalid';

INSERT INTO public.app_consump_shape (consump_id, month, day_type, hour, value_pu)
SELECT c.consump_id, month, day_type, hour,
       CASE WHEN hour BETWEEN 7 AND 21 THEN 1.25 ELSE .55 END
FROM public.app_consump_inst c
CROSS JOIN generate_series(1, 12) AS month
CROSS JOIN (VALUES ('laborable'), ('finde')) AS kinds(day_type)
CROSS JOIN generate_series(0, 23) AS hour
WHERE c.code = 'CONSUMO-TEST';
UPDATE public.app_consump_shape
SET value_pu = value_pu / (SELECT avg(value_pu) FROM public.app_consump_shape);

INSERT INTO public.app_gen_shape (gen_id, month, day_type, hour, value_pu)
SELECT g.gen_id, month, day_type, hour,
       CASE WHEN hour BETWEEN 8 AND 17 THEN greatest(0, sin(pi() * (hour - 7) / 11)) ELSE 0 END
FROM public.app_gen_inst g
CROSS JOIN generate_series(1, 12) AS month
CROSS JOIN (VALUES ('laborable'), ('finde')) AS kinds(day_type)
CROSS JOIN generate_series(0, 23) AS hour
WHERE g.code = 'FV-TEST';
UPDATE public.app_gen_shape
SET value_pu = value_pu / (SELECT avg(value_pu) FROM public.app_gen_shape);

INSERT INTO public.app_study_case (
    user_id, code, name, mode, battery_id, consump_id, gen_id, date_from, date_to,
    charge_policy, window_days, discount_rate, opex_pct
)
SELECT u.user_id, 'SNAPSHOT-TEST', 'Prueba de parámetros guardados', 'autoconsumo',
       b.battery_id, c.consump_id, g.gen_id, DATE '2026-01-05', DATE '2026-01-06',
       'libre', 1, .07, .015
FROM public.app_user u
JOIN public.app_battery_model b USING (user_id)
JOIN public.app_consump_inst c USING (user_id)
JOIN public.app_gen_inst g USING (user_id)
WHERE u.email = 'snapshot-test@local.invalid';

DO $$
BEGIN
    IF (SELECT count(*) FROM public.spot_price) <> 48
       OR (SELECT count(*) FROM public.app_consump_shape) <> 576
       OR (SELECT count(*) FROM public.app_gen_shape) <> 576
       OR (SELECT count(*) FROM public.app_study_case) <> 1 THEN
        RAISE EXCEPTION 'Synthetic fixture is incomplete';
    END IF;
END;
$$;
