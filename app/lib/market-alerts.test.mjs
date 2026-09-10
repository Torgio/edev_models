import assert from 'node:assert/strict';
import test from 'node:test';
import { marketAlerts, pairedDifference, preferredPriceSeries } from './market-alerts.ts';

test('uses a complete real curve and otherwise keeps one forecast source', () => {
  const complete = [{ actual: 10, predictions: { model: 20 } }, { actual: 30, predictions: { model: 40 } }];
  assert.deepEqual(preferredPriceSeries(complete, 'model'), { kind: 'real', values: [10, 30] });
  assert.deepEqual(preferredPriceSeries([{ ...complete[0], actual: null }, complete[1]], 'model'), { kind: 'previsto', values: [20, 40] });
});

test('creates only threshold alerts and retains their market-hour positions', () => {
  const alerts = marketAlerts([-2, 20, 85, 155], [40, 20, 30, 100]);
  assert.deepEqual(alerts.map(alert => alert.id), ['peak', 'ramp', 'negative', 'difference']);
  assert.deepEqual(alerts.find(alert => alert.id === 'ramp'), { id: 'ramp', title: 'Rampa rápida', detail: 'Subida de 70.00 €/MWh', from: 2, to: 3, value: 70 });
  assert.deepEqual(marketAlerts([10, 20, 30], [11, 19, 31]), []);
});

test('daily comparison only averages paired market hours', () => {
  assert.deepEqual(pairedDifference([10, null, 30], [5, 20, 20]), { difference: 7.5, hours: 2 });
  assert.deepEqual(pairedDifference([null], [2]), { difference: null, hours: 0 });
});
