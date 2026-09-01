import { test } from 'node:test';
import assert from 'node:assert/strict';
import { predictionUpdate } from './prediction-update.ts';

test('shows the full Madrid calendar date, not just a potentially misleading hour', () => {
  const text = predictionUpdate('2026-08-30T23:30:00Z', true);
  assert.match(text, /31\/08\/2026/);
  assert.match(text, /01:30/);
  assert.match(text, /Madrid/);
});
test('uses winter offset as well as summer offset', () => {
  assert.match(predictionUpdate('2026-01-01T12:00:00Z', true), /13:00/);
  assert.match(predictionUpdate('2026-08-01T12:00:00Z', true), /14:00/);
});
test('absent, invalid and non-current timestamps do not appear as current updates', () => {
  assert.equal(predictionUpdate(null, true), null);
  assert.equal(predictionUpdate('invalid', true), null);
  assert.equal(predictionUpdate('2026-08-30T23:30:00Z', false), null);
});
