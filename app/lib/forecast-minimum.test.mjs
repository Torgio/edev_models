import { test } from 'node:test';
import assert from 'node:assert/strict';
import { forecastMinimum } from './forecast-minimum.ts';

test('minimum follows the forecast, with no fixed afternoon window', () => {
  const result = forecastMinimum([
    { hour: '04:00', predictions: { ensemble: -2 } },
    { hour: '13:00', predictions: { ensemble: 10 } },
  ]);
  assert.deepEqual(result.minimum, { index: 0, hour: '04:00', value: -2 });
});
test('counts actual models, including unknown identifiers, only once', () => {
  const predictions = Object.fromEntries(Array.from({ length: 13 }, (_, i) => [`model_${i}`, i]));
  assert.equal(forecastMinimum([{ hour: '01:00', predictions }, { hour: '02:00', predictions }]).modelCount, 13);
});
test('missing reference never silently switches to another model', () => {
  const result = forecastMinimum([{ hour: '01:00', predictions: { gru: 1, ensemble: null } }]);
  assert.equal(result.minimum, null);
  assert.equal(result.modelCount, 1);
});
test('ignores missing/nonfinite values while retaining zero and partial coverage', () => {
  const result = forecastMinimum([null, NaN, Infinity, 0].map((value, i) => ({ hour: `${i}:00`, predictions: { ensemble: value } })));
  assert.equal(result.minimum.value, 0);
  assert.equal(result.observations, 1);
  assert.equal(result.totalHours, 4);
  assert.equal(forecastMinimum([]).minimum, null);
});
test('repeated clock labels retain distinct chart positions', () => {
  const result = forecastMinimum([
    { hour: '02:00', predictions: { ensemble: 5 } },
    { hour: '02:00', predictions: { ensemble: 1 } },
  ]);
  assert.equal(result.minimum.index, 1);
});
test('ties retain the first point and report multiple minima, not a charging window', () => {
  const result = forecastMinimum(['03:00', '15:00'].map(hour => ({ hour, predictions: { ensemble: 1 } })));
  assert.equal(result.ties, 2);
  assert.equal(result.minimum.hour, '03:00');
});
