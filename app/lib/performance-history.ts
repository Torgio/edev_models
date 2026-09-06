export type PerformanceModel = {
  model: string; seed: number; days: number; start_date: string; end_date: string;
};

export type PerformancePoint = {
  date: string; n_obs: number; mae: number; mae_naive: number;
  skill_vs_naive: number | null; skill_7d: number | null;
  estado: string; dias_en_ventana: number | null;
};

export type PerformanceSummary = {
  start_date: string; end_date: string; window_days: number; evaluated_days: number;
  observations: number; days_won: number; skill_pct: number | null;
  recent_days: number; recent_evaluated_days: number; recent_skill_pct: number | null;
  first_half_skill_pct: number | null; second_half_skill_pct: number | null;
};

export type PerformancePayload = {
  origin: 'model_metrics_daily'; model: string; seed: number; source: 'production';
  available: PerformanceModel[]; summary: PerformanceSummary;
  series: PerformancePoint[]; naive_rule: string | null; definition: string;
};

export const performanceIdentity = (model: string, seed: number) => `${encodeURIComponent(model)}:${seed}`;
export function parsePerformanceIdentity(value: string) {
  const separator = value.lastIndexOf(':');
  if (separator < 1) return null;
  const seed = Number(value.slice(separator + 1));
  if (!Number.isInteger(seed)) return null;
  try { return { model: decodeURIComponent(value.slice(0, separator)), seed }; }
  catch { return null; }
}

export function clippedSkill(value: number | null, limit = 80) {
  if (value === null || !Number.isFinite(value)) return null;
  return Math.max(-limit, Math.min(limit, value));
}
