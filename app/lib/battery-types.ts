export type BatteryAssumptions = Record<string, unknown> | null;
export type BatteryPlan = {
  datetime: string; model: string; carga_mw: number; descarga_mw: number;
  soc_mwh: number | null; ingreso_eur: number | null;
  simulador: BatteryAssumptions; updated_at: string;
};
export type BatteryResult = {
  model: string; ingreso_eur: number | null; ingreso_oraculo_eur: number | null;
  ingreso_naive_eur: number | null; captura_pct: number | null; ciclos: number | null;
  simulador: BatteryAssumptions; calculado_en: string;
};
export type BatteryPayload = { date: string; plan: BatteryPlan[]; results: BatteryResult[] };
