import assert from 'node:assert/strict';
import test from 'node:test';

import { forecastRamp, negativePriceHours } from './market-signals.ts';

const hours = [
  { actual: 4, predictions: { model: 5 } },
  { actual: -2, predictions: { model: -3 } },
  { actual: 8, predictions: { model: 9 } },
  { actual: null, predictions: { model: 7 } },
];

test('the largest forecast ramp uses consecutive stored hours', () => {
  assert.deepEqual(forecastRamp(hours, 'model'), { from: 1, to: 2, increase: 12, value: 9 });
});

test('negative hours distinguish forecast from actual without inventing values', () => {
  assert.deepEqual(negativePriceHours(hours, 'model'), {
    entries: [{ index: 1, predicted: true, actual: true }], predicted: 1, actual: 1,
  });
  assert.deepEqual(negativePriceHours(hours, 'missing'), { entries: [{ index: 1, predicted: false, actual: true }], predicted: 0, actual: 1 });
});
