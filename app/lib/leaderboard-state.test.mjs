import { test } from 'node:test';
import assert from 'node:assert/strict';
import { currentLeaders } from './leaderboard-state.ts';

const day = '2026-08-31';
const real = [{ model: 'ensemble', mae: 0, observations: 24, updated_at: day }];

test('empty real response remains empty: no reserve ranking or winner', () => {
  const rows = currentLeaders([], 'live', day, day);
  assert.deepEqual(rows, []);
  assert.equal(rows[0], undefined);
});
test('loading, failure and demonstration hide previous results', () => {
  for (const status of ['loading', 'error', 'demo']) {
    assert.deepEqual(currentLeaders(real, status, day, day), []);
  }
});
test('changing day hides results before the next fetch effect runs', () => {
  assert.deepEqual(currentLeaders(real, 'live', day, '2026-09-01'), []);
  assert.deepEqual(currentLeaders(real, 'live', undefined, day), []);
});
test('a successful current response preserves real rows, including Ensemble and zero MAE', () => {
  assert.strictEqual(currentLeaders(real, 'live', day, day), real);
});
