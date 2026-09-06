import test from 'node:test';
import assert from 'node:assert/strict';
import { bestMae, evaluationGroup, rankedEvaluations, metric } from './stored-evaluations.ts';

const row = (overrides = {}) => ({ model: 'a', seed: 42, periodo: 'test', corte: 'global', simulador: { eficiencia: .9 }, n_obs: 40, mae: 12, captura_pct: 90, skill_vs_naive: 4, ...overrides });

test('empty and null metrics have no reserve winner, zero is real', () => {
  assert.equal(bestMae([]), undefined);
  assert.equal(bestMae([row({ mae: null })]), undefined);
  assert.equal(bestMae([row({ n_obs: 0 })]), undefined);
  assert.equal(bestMae([row({ mae: 0 })]).mae, 0);
  assert.equal(metric(null), '—');
  assert.equal(metric(NaN), '—');
  assert.equal(metric(0), '0,00');
});
test('periods, slices and simulator settings are never merged', () => {
  const first = row();
  const rows = [first, row({ periodo: 'validation' }), row({ corte: 'peak' }), row({ simulador: { eficiencia: .8 } })];
  assert.deepEqual(rankedEvaluations(rows, evaluationGroup(first), 'mae'), [first]);
  assert.equal(evaluationGroup(row({ simulador: { a: 1, b: 2 } })), evaluationGroup(row({ simulador: { b: 2, a: 1 } })));
});
test('seeds and stored statuses are preserved; sort is not model adoption', () => {
  const rows = [row({ seed: 42, estado: 'retirado', mae: 8 }), row({ seed: 43, estado: 'retador', mae: 10 })];
  const ranked = rankedEvaluations(rows, evaluationGroup(rows[0]), 'mae');
  assert.equal(ranked.length, 2);
  assert.equal(ranked[0].estado, 'retirado');
  assert.deepEqual(ranked.map(x => x.seed), [42, 43]);
});
test('MAE and capture order differ; missing metrics sort last without zero imputation', () => {
  const rows = [row({ model: 'precise', mae: 8, captura_pct: 80 }), row({ model: 'valuable', mae: 20, captura_pct: 95 }), row({ model: 'missing', mae: null, captura_pct: null })];
  const group = evaluationGroup(rows[0]);
  assert.deepEqual(rankedEvaluations(rows, group, 'mae').map(x => x.model), ['precise', 'valuable', 'missing']);
  assert.deepEqual(rankedEvaluations(rows, group, 'captura_pct').map(x => x.model), ['valuable', 'precise', 'missing']);
  assert.equal(bestMae(rows).model, 'precise');
});
