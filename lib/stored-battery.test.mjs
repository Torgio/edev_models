import test from 'node:test';
import assert from 'node:assert/strict';
import { batteryModels, preferredBatteryModel, storedPlanSummary } from './stored-battery.ts';

test('stored income retains charges, discharges and zero without rounding early', () => {
  const result = storedPlanSummary([
    { ingreso_eur: -18.722488, soc_mwh: 1 },
    { ingreso_eur: -13.885167, soc_mwh: 2 },
    { ingreso_eur: 0, soc_mwh: 2 },
    { ingreso_eur: 167.233446, soc_mwh: 1 },
    { ingreso_eur: 164.508606, soc_mwh: 0 },
  ]);
  assert.equal(result.income, 299.134397);
  assert.ok(Math.abs(result.chargeCost - -32.607655) < 1e-9);
  assert.ok(Math.abs(result.dischargeRevenue - 331.742052) < 1e-9);
  assert.equal(result.maxSoc, 2);
  assert.equal(result.observations, 5);
});

test('missing stored values make aggregates unavailable rather than imputing zero', () => {
  assert.deepEqual(storedPlanSummary([]), { income: null, chargeCost: null, dischargeRevenue: null, maxSoc: null, observations: 0 });
  assert.equal(storedPlanSummary([{ ingreso_eur: null, soc_mwh: 0 }]).income, null);
  assert.equal(storedPlanSummary([{ ingreso_eur: 0, soc_mwh: null }]).maxSoc, null);
});

test('battery models are discovered from both tables without a fixed catalogue', () => {
  assert.deepEqual(batteryModels([{ model: 'ensemble' }, { model: 'new' }], [{ model: 'new' }, { model: 'gru' }]), ['ensemble', 'gru', 'new']);
});

test('battery opens on the stored operational plan instead of the first alphabetic result', () => {
  const plan = [{ model: 'ensemble11' }];
  const results = [{ model: 'boosting' }, { model: 'ensemble11' }, { model: 'gru' }];
  assert.equal(preferredBatteryModel(plan, results), 'ensemble11');
});

test('battery preserves a valid explicit model choice even when it has no hourly plan', () => {
  const plan = [{ model: 'ensemble11' }];
  const results = [{ model: 'boosting' }, { model: 'ensemble11' }];
  assert.equal(preferredBatteryModel(plan, results, 'boosting'), 'boosting');
});

test('battery falls back across legacy ensemble names and result-only dates', () => {
  assert.equal(preferredBatteryModel([{ model: 'ensemble' }], [{ model: 'boosting' }]), 'ensemble');
  assert.equal(preferredBatteryModel([], [{ model: 'boosting' }, { model: 'ensemble11' }]), 'ensemble11');
});
