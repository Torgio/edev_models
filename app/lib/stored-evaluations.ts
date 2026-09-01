export type Evaluation = {
  model: string; seed: number; periodo: string; corte: string; n_obs: number | null;
  mae: number | null; captura_pct: number | null; skill_vs_naive: number | null;
  pico_1h_pct: number | null; cobertura_ic80: number | null;
  simulador: Record<string, unknown> | null; estado: string | null; calculado_en: string;
};
export type Order = 'mae' | 'captura_pct' | 'skill_vs_naive';
export const numeric = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
export const metric = (value: unknown, suffix = '') => numeric(value)
  ? `${value.toLocaleString('es-ES', { maximumFractionDigits: 2, minimumFractionDigits: 2 })}${suffix}` : '—';
function canonical(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return JSON.stringify(value.map(canonical));
  return JSON.stringify(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, canonical(v)]));
}
// Never merge seeds, periods, slices or different simulator assumptions.
export const evaluationGroup = (row: Evaluation) => canonical([row.periodo, row.corte, row.simulador]);
export function rankedEvaluations(rows: Evaluation[], group: string, order: Order) {
  return rows.filter(row => evaluationGroup(row) === group).sort((a, b) => {
    const left = a[order], right = b[order];
    if (!numeric(left)) return numeric(right) ? 1 : a.model.localeCompare(b.model) || a.seed - b.seed;
    if (!numeric(right)) return -1;
    return (order === 'mae' ? left - right : right - left) || a.model.localeCompare(b.model) || a.seed - b.seed;
  });
}
export function bestMae(rows: Evaluation[]) {
  return rows.filter(row => numeric(row.mae) && row.mae >= 0 && numeric(row.n_obs) && row.n_obs > 0)
    .sort((a, b) => a.mae! - b.mae!)[0];
}
