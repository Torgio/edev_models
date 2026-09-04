import test from 'node:test';
import assert from 'node:assert/strict';
import { clippedSkill, parsePerformanceIdentity, performanceIdentity, preferredPerformanceIdentity } from './performance-history.ts';

test('chart clipping preserves the real metric outside the display helper', () => {
  assert.equal(clippedSkill(-233.2), -80);
  assert.equal(clippedSkill(45.6), 45.6);
  assert.equal(clippedSkill(null), null);
});

test('model and seed stay one explicit identity', () => {
  const value = performanceIdentity('lightgbm:núcleo', 42);
  assert.deepEqual(parsePerformanceIdentity(value), { model: 'lightgbm:núcleo', seed: 42 });
  assert.equal(parsePerformanceIdentity('invalid'), null);
});

test('an unavailable default falls back to the highest-priority stored series', () => {
  const available = [
    { model: 'boosting', seed: 42, days: 30, start_date: '2026-08-03', end_date: '2026-09-01' },
    { model: 'gru', seed: 7, days: 20, start_date: '2026-08-13', end_date: '2026-09-01' },
  ];
  assert.equal(preferredPerformanceIdentity(available, performanceIdentity('gru', 44)), performanceIdentity('boosting', 42));
  assert.equal(preferredPerformanceIdentity(available, performanceIdentity('gru', 7)), performanceIdentity('gru', 7));
});
