import test from 'node:test';
import assert from 'node:assert/strict';
import { priceAxisLower, priceAxisTick, priceAxisUpper } from './price-axis.ts';

test('price axis expands outward to clean ten-euro limits', () => {
  assert.equal(priceAxisLower(0.32), -10);
  assert.equal(priceAxisLower(-7.68), -20);
  assert.equal(priceAxisUpper(205.91623), 220);
});

test('price axis ticks never expose floating-point noise', () => {
  assert.equal(priceAxisTick(213.91623), '214');
  assert.equal(priceAxisTick(-7.68), '-8');
});
