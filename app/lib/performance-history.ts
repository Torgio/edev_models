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
  first_evaluated_date?: string | null; last_evaluated_date?: string | null;
  expected_through_date?: string | null; lag_days?: number | null; is_stale?: boolean | null;
  observations: number; days_won: number; skill_pct: number | null;
  recent_days: number; recent_evaluated_days: number; recent_skill_pct: number | null;
  first_half_skill_pct: number | null; second_half_skill_pct: number | null;
};

export type PerformancePayload = {
  origin: 'model_metrics_daily'; model: string; seed: number; source: 'production';
  available: PerformanceModel[]; summary: PerformanceSummary;
  series: PerformancePoint[]; naive_rule: string | null; definition: string;
};

export type PerformanceOptionsPayload = {
  origin: 'model_metrics_daily'; source: 'production'; available: PerformanceModel[];
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

export function preferredPerformanceIdentity(available: PerformanceModel[], current: string) {
  const selected = parsePerformanceIdentity(current);
  const valid = selected && available.some(row => row.model === selected.model && row.seed === selected.seed);
  return valid || !available.length
    ? current
    : performanceIdentity(available[0].model, available[0].seed);
}

export function clippedSkill(value: number | null, limit = 80) {
  if (value === null || !Number.isFinite(value)) return null;
  return Math.max(-limit, Math.min(limit, value));
}

export function performanceTone(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return 'unavailable';
  return value >= 0 ? 'good' : 'bad';
}

type ActualCoverage = { date: string; actual_hours: number; expected_hours: number };

export type PerformanceFreshness = {
  firstEvaluated: string | null;
  lastEvaluated: string | null;
  expectedThrough: string | null;
  lagDays: number | null;
  isStale: boolean | null;
};

const validDay = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);

export function latestCompleteActualDay(days: ActualCoverage[]) {
  return days
    .filter(day => validDay(day.date) && day.expected_hours > 0 && day.actual_hours === day.expected_hours)
    .map(day => day.date)
    .sort()
    .at(-1) ?? null;
}

export function performanceFreshness(
  summary: PerformanceSummary,
  series: PerformancePoint[],
  expectedFallback: string | null,
): PerformanceFreshness {
  const seriesDates = series.map(row => row.date).filter(validDay).sort();
  const firstEvaluated = validDay(summary.first_evaluated_date)
    ? summary.first_evaluated_date : seriesDates[0] ?? null;
  const lastEvaluated = validDay(summary.last_evaluated_date)
    ? summary.last_evaluated_date : seriesDates.at(-1) ?? null;
  const expectedThrough = validDay(summary.expected_through_date)
    ? summary.expected_through_date : validDay(expectedFallback) ? expectedFallback : null;
  if (!lastEvaluated || !expectedThrough) {
    return { firstEvaluated, lastEvaluated, expectedThrough, lagDays: null, isStale: null };
  }
  const lagDays = Math.max(0, Math.round(
    (Date.parse(`${expectedThrough}T00:00:00Z`) - Date.parse(`${lastEvaluated}T00:00:00Z`)) / 86_400_000,
  ));
  return { firstEvaluated, lastEvaluated, expectedThrough, lagDays, isStale: lagDays > 0 };
}
