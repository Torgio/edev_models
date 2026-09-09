import test from 'node:test';
import assert from 'node:assert/strict';
import {
  clippedSkill, latestCompleteActualDay, parsePerformanceIdentity, performanceFreshness,
  performanceIdentity, preferredPerformanceIdentity,
} from './performance-history.ts';

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

test('latest complete actual day does not depend on the dashboard closed flag', () => {
  const days = [
    { date: '2026-09-08', actual_hours: 24, expected_hours: 24, closed: true },
    { date: '2026-09-09', actual_hours: 24, expected_hours: 24, closed: false },
    { date: '2026-09-10', actual_hours: 0, expected_hours: 24, closed: false },
  ];
  assert.equal(latestCompleteActualDay(days), '2026-09-09');
});

test('freshness falls back to stored series and existing day coverage', () => {
  const summary = { start_date: '2026-08-07', end_date: '2026-09-05' };
  const series = [{ date: '2026-08-07' }, { date: '2026-09-05' }];
  assert.deepEqual(performanceFreshness(summary, series, '2026-09-09'), {
    firstEvaluated: '2026-08-07', lastEvaluated: '2026-09-05',
    expectedThrough: '2026-09-09', lagDays: 4, isStale: true,
  });
});
