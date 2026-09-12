export const MODEL_GROUPS = ['arboles', 'redes', 'ensembles'] as const;
export type ModelGroup = (typeof MODEL_GROUPS)[number];

export const GROUP_LABELS: Record<ModelGroup, string> = {
  arboles: 'Árboles', redes: 'Redes', ensembles: 'Ensembles',
};

export const MODEL_STYLES = [
  { key: 'lgbm_nucleo', label: 'LightGBM núcleo', color: '#1f6b43', group: 'arboles' },
  { key: 'lightgbm', label: 'LightGBM', color: '#3f9160', group: 'arboles' },
  { key: 'xgboost', label: 'XGBoost', color: '#6cb37f', group: 'arboles' },
  { key: 'boosting', label: 'Boosting', color: '#9ccf9e', group: 'arboles' },
  { key: 'gru', label: 'GRU', color: '#1b5aa0', group: 'redes' },
  { key: 'lstm', label: 'LSTM', color: '#4680c6', group: 'redes' },
  { key: 'conv1d_lstm', label: 'Conv1D-LSTM', color: '#74a6de', group: 'redes' },
  { key: 'simplernn', label: 'SimpleRNN', color: '#a3c6ef', group: 'redes' },
  { key: 'denso', label: 'Denso', color: '#7d76cc', group: 'redes' },
  { key: 'seq2seq', label: 'Seq2Seq', color: '#9d5fbe', group: 'redes' },
  { key: 'seq2seq_absoluto', label: 'Seq2Seq absoluto', color: '#c273b2', group: 'redes' },
  { key: 'ensemble', label: 'Ensemble', color: '#0d7b74', group: 'ensembles' },
  { key: 'ensemble11', label: 'Ensemble 11', color: '#4fb8ae', group: 'ensembles' },
] as const satisfies ReadonlyArray<{ key: string; label: string; color: string; group: ModelGroup }>;

export const ANNOTATION_COLORS = {
  mark: '#e58b45',              // valle previsto, pico, alerta seleccionada
  negative: '#b85f3b',          // horas reales bajo cero
  negativeForecast: '#e8a36f',  // horas bajo cero solo previstas
  actual: '#142e28',            // precio real
  spread: '#6b7f78',            // banda de dispersión entre modelos
  ramp: '#a8842c',              // mayor rampa prevista
  comparison: '#5f736c',        // serie del día comparado
} as const;

const FAINT = new Set(['#a3c6ef', '#9ccf9e', '#74a6de', '#c273b2']);
export const seriesOpacity = (color: string) => (FAINT.has(color) ? 0.9 : 0.55);

/** Use the curated palette first; hash only genuinely new model identifiers. */
export function modelColor(model: string) {
  const known = MODEL_STYLES.find(entry => entry.key === model);
  if (known) return known.color;
  const hue = [...model].reduce((value, letter) => (value * 31 + letter.charCodeAt(0)) % 360, 0);
  return `hsl(${hue} 46% 46%)`;
}

export function modelGroup(model: string): ModelGroup {
  return MODEL_STYLES.find(entry => entry.key === model)?.group ?? 'redes';
}

export function groupedModels(available: string[]) {
  const set = new Set(available);
  const known = MODEL_STYLES.filter(entry => set.has(entry.key));
  const unknown = available
    .filter(key => !MODEL_STYLES.some(entry => entry.key === key))
    .map(key => ({ key, label: key, color: modelColor(key), group: 'redes' as ModelGroup }));
  return MODEL_GROUPS.map(group => ({
    group, label: GROUP_LABELS[group],
    models: [...known, ...unknown].filter(entry => entry.group === group),
  })).filter(entry => entry.models.length > 0);
}
