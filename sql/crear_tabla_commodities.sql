-- TFM Energia UCM — Tabla de precios diarios de commodities energeticas
-- Fuentes: Yahoo Finance (CO2_ets, gas_TTF), MIBGAS (gas_MIBGAS), manual (carbon_API2)

CREATE TABLE IF NOT EXISTS public.commodities (
    fecha        DATE    NOT NULL,
    "CO2_ets"    NUMERIC,   -- Precio CO2 ETS €/tonelada (EUA) — Yahoo Finance CO2.L
    gas_TTF      NUMERIC,   -- Precio gas TTF €/MWh — Yahoo Finance TTF=F
    gas_MIBGAS   NUMERIC,   -- Precio gas MIBGAS €/MWh — mibgas.es
    carbon_API2  NUMERIC,   -- Precio carbon API2 $/tonelada
    CONSTRAINT commodities_pkey PRIMARY KEY (fecha)
);