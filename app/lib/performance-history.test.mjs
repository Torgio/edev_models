import test from 'node:test';
import assert from 'node:assert/strict';
import { clippedSkill, parsePerformanceIdentity, performanceIdentity } from './performance-history.ts';

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
