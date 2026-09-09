import contextlib
from datetime import date
import io
import json
import os
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

import numpy as np
import pandas as pd

from production.app.run_snapshot import capture_inputs, public_inputs


class SnapshotTests(unittest.TestCase):
    def inputs(self):
        return dict(
            case=dict(case_id=np.int64(13), code='A', name='Fábrica', mode='autoconsumo',
                      date_from=date(2027, 1, 1), date_to=date(2027, 12, 31),
                      window_days=7, discount_rate=.07, opex_pct=.015, user_id=999),
            battery=dict(battery_id=2, code='BAT', name='Batería', source_file='/private/path'),
            effective_battery=dict(potencia_mw=.05, duracion_h=4., capex_eur_mwh=200000., eficiencia_rt=.9),
            site=dict(recargo_tarifa=70.),
            consumption=dict(annual_mwh=np.float64(350), contracted_power_mw=np.nan, email='private'),
            generation=dict(capacity_mwp=.25, technology='fv'),
            consumption_shape=[(1, 'laborable', 0, np.float32(.8))], generation_shape=[],
            date_from=date(2027, 1, 1), date_to=date(2027, 12, 31),
            cycle_cost=35., scenarios=20, generation_full_load_hours=1600)

    def test_detached_inputs_units_dates_and_no_private_fields(self):
        args = self.inputs()
        saved = capture_inputs(**args)
        args['effective_battery']['potencia_mw'] = 5
        args['consumption']['annual_mwh'] = 999
        args['consumption_shape'].clear()
        self.assertEqual(saved['battery']['power_mw'], .05)
        self.assertEqual(saved['battery']['capacity_mwh'], .2)
        self.assertEqual(saved['consumption']['annual_mwh'], 350)
        self.assertEqual(saved['period']['date_from'], '2027-01-01')
        self.assertEqual(len(saved['profiles']['consumption']), 1)
        self.assertIsNone(saved['consumption']['contracted_power_mw'])
        serialized = json.dumps(saved, allow_nan=False)
        for secret in ('user_id', 'email', '/private/path'):
            self.assertNotIn(secret, serialized)

    def test_absent_installation_and_legacy_are_distinct(self):
        args = self.inputs()
        args.update(consumption=None, generation=None, consumption_shape=None, generation_shape=None)
        args['case']['mode'] = 'standalone'
        public = public_inputs(capture_inputs(**args))
        self.assertIsNone(public['consumption'])
        self.assertIsNone(public['generation'])
        self.assertNotIn('profiles', public)
        self.assertIsNone(public_inputs(None))
        self.assertIsNone(public_inputs({'schema_version': 99}))

    def test_result_api_returns_saved_inputs_without_catalog_joins(self):
        from production.api import bateria
        saved = capture_inputs(**self.inputs())
        for has_column, snapshot in ((True, saved), (True, None), (False, None)):
            cur = MagicMock()
            cur.description = [('run_id',), ('case_id',)] + ([('input_snapshot',)] if has_column else [])
            cur.fetchone.return_value = (16, 13, snapshot) if has_column else (16, 13)
            cur.fetchall.return_value = []
            with patch.object(bateria, 'cursor', return_value=contextlib.nullcontext(cur)):
                result = bateria.resultado(16)
            self.assertEqual(result['inputs'], public_inputs(snapshot))
            self.assertNotIn('input_snapshot', result['run'])
            self.assertEqual(cur.execute.call_count, 2)
            self.assertTrue(all('JOIN' not in call.args[0] for call in cur.execute.call_args_list))

    def test_battery_api_database_override_only_accepts_test_database(self):
        from production.api import bateria
        import config
        db = {'host': 'db.local', 'port': 5432, 'dbname': 'tfm_energia',
              'user': 'reader', 'password': 'secret'}
        fake_pool = MagicMock()
        with patch.object(config, 'load_config', return_value=({}, db)), \
             patch('psycopg2.pool.ThreadedConnectionPool', return_value=fake_pool) as pool, \
             patch.dict(os.environ, {'TFM_TEST_DB_NAME': 'tfm_energia_test'}):
            self.assertIs(bateria._crear_pool(), fake_pool)
        self.assertEqual(pool.call_args.kwargs['dbname'], 'tfm_energia_test')
        self.assertEqual(db['dbname'], 'tfm_energia')
        with patch.object(config, 'load_config', return_value=({}, db)), \
             patch.dict(os.environ, {'TFM_TEST_DB_NAME': 'tfm_energia'}), \
             self.assertRaisesRegex(RuntimeError, 'terminados en _test'):
            bateria._crear_pool()

    def test_missing_migration_fails_before_reading_inputs_or_solving(self):
        from production.app import caso
        from psycopg2.errors import UndefinedColumn
        cur = MagicMock()
        cur.execute.side_effect = UndefinedColumn('missing input_snapshot')
        with patch.object(caso.pd, 'read_sql') as read, self.assertRaisesRegex(SystemExit, '20260909_run_input_snapshot.sql'):
            caso.cmd_ejecutar(MagicMock(), cur, 1, SimpleNamespace(code='A'))
        read.assert_not_called()

    def test_execution_inserts_snapshot_in_same_run_before_solver_mutations(self):
        from production.app import caso
        from psycopg2.extras import Json
        import optimiza_bateria
        c = dict(case_id=13, code='A', name='Caso', mode='standalone', battery_id=2,
                 date_from=date(2027, 1, 1), date_to=date(2027, 1, 2), cycle_cost_eur_mwh=0.,
                 window_days=7, charge_policy='libre', discount_rate=.07, opex_pct=.015)
        b = dict(battery_id=2, code='BAT', name='Batería', power_mw=.05, duration_h=4.,
                 charge_max_pct=100., discharge_max_pct=100., power_min_pct=0., efficiency_rt=.9,
                 soc_min=.1, soc_max=1., cycle_life=6000, degradation_per_1000=2.,
                 capex_eur_mwh=200000., degradation_annual=2.)
        days = pd.date_range('2027-01-01', periods=2)
        cur = MagicMock()
        cur.fetchone.side_effect = [(None, None), (16,)]

        def solve(px, bat, *args, **kwargs):
            bat['potencia_mw'] = .1  # Simulates a later mutation; saved inputs must stay .05.
            return pd.DataFrame({key: [1., 1.] for key in (
                'margen_eur', 'ciclos', 'carga_mwh', 'descarga_mwh', 'importado_mwh', 'exportado_mwh')}), {}

        with patch.object(caso.pd, 'read_sql', side_effect=[pd.DataFrame([c]), pd.DataFrame([b])]), \
             patch.object(caso, 'precios', return_value=(np.ones((1, 2, 24)), days, pd.Series(['simulado']*2, index=days), date(2027, 1, 1))), \
             patch.object(optimiza_bateria, 'optimizar', side_effect=solve), \
             patch.object(caso, 'execute_values'), contextlib.redirect_stdout(io.StringIO()):
            caso.cmd_ejecutar(MagicMock(), cur, 1, SimpleNamespace(code='A', escenarios=1, notas=None, guardar_despacho=False))
        call = next(call for call in cur.execute.call_args_list if call.args[0].startswith('INSERT INTO app_case_run'))
        self.assertIn('input_snapshot', call.args[0])
        snapshot = call.args[1][-1]
        self.assertIsInstance(snapshot, Json)
        self.assertEqual(snapshot.adapted['battery']['power_mw'], .05)
        self.assertEqual(snapshot.adapted['period']['date_to'], '2027-01-02')


if __name__ == '__main__':
    unittest.main()
