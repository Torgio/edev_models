import assert from 'node:assert/strict';
import test from 'node:test';

import { modelsToPlot } from './visible-models.ts';

test('the default chart always includes the reference model', () => {
  assert.deepEqual(
    modelsToPlot(['boosting', 'conv1d_lstm', 'denso', 'ensemble'], [], 'ensemble'),
    ['ensemble', 'boosting', 'conv1d_lstm'],
  );
});

test('a new reference joins an explicit selection', () => {
  assert.deepEqual(
    modelsToPlot(['boosting', 'denso', 'ensemble'], ['boosting', 'denso'], 'ensemble'),
    ['ensemble', 'boosting', 'denso'],
  );
});

test('unavailable and repeated explicit models are discarded', () => {
  assert.deepEqual(
    modelsToPlot(['gru', 'ensemble'], ['gru', 'gru', 'missing'], 'ensemble'),
    ['ensemble', 'gru'],
  );
});
