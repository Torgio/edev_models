export const MODEL_STYLES = [
  { key: 'ensemble', label: 'Ensemble', color: '#e58b45' },
  { key: 'gru', label: 'GRU', color: '#43a99f' },
  { key: 'boosting', label: 'Boosting', color: '#7b8ee8' },
  { key: 'seq2seq', label: 'Seq2Seq', color: '#cf6f87' },
  { key: 'denso', label: 'Denso', color: '#b178d3' },
  { key: 'simplernn', label: 'SimpleRNN', color: '#d19a3a' },
  { key: 'conv1d_lstm', label: 'Conv1D-LSTM', color: '#4f8fbe' },
  { key: 'lstm', label: 'LSTM', color: '#829557' },
  { key: 'seq2seq_absoluto', label: 'Seq2Seq absoluto', color: '#bf685f' },
  { key: 'ensemble11', label: 'Ensemble 11', color: '#d8783e' },
  { key: 'lgbm_nucleo', label: 'LightGBM núcleo', color: '#4f9b68' },
  { key: 'lightgbm', label: 'LightGBM', color: '#6aa84f' },
  { key: 'xgboost', label: 'XGBoost', color: '#8f6ab8' },
] as const;

/** Use the curated palette first; hash only genuinely new model identifiers. */
export function modelColor(model: string) {
  const known = MODEL_STYLES.find(entry => entry.key === model);
  if (known) return known.color;
  const hue = [...model].reduce((value, letter) => (value * 31 + letter.charCodeAt(0)) % 360, 0);
  return `hsl(${hue} 46% 46%)`;
}
