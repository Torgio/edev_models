import unittest
from datetime import date
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from api.dashboard_api import app, _connection
from api.stored_results import clean, performance_summary, weighted_skill


class StoredResultsTests(unittest.TestCase):
    def setUp(self):
        self.auth = patch('api.dashboard_api.auth_config', return_value=None)
        self.connect = patch('api.dashboard_api._connection')
        self.auth.start()
        connect = self.connect.start()
        self.addCleanup(self.auth.stop)
        self.addCleanup(self.connect.stop)
        self.cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        self.client = TestClient(app)

    def test_evaluations_preserve_seeds_periods_nulls_and_status(self):
        names = ['model', 'seed', 'periodo', 'corte', 'n_obs', 'mae', 'captura_pct', 'estado']
        self.cursor.description = [(name,) for name in names]
        self.cursor.fetchall.return_value = [
            ('one', 42, 'test_2026', 'global', 5000, 0.0, None, 'retador'),
            ('one', 43, 'val_2025', 'global', 8700, 18.0, 93.0, 'retirado'),
        ]
        response = self.client.get('/leaderboard')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['origin'], 'model_metrics')
        self.assertEqual(len(data['models']), 2)
        self.assertEqual(data['models'][0]['mae'], 0)
        self.assertIsNone(data['models'][0]['captura_pct'])
        self.assertEqual(data['models'][1]['estado'], 'retirado')
        query = self.cursor.execute.call_args.args[0]
        self.assertIn('m.seed = e.seed', query)
        self.assertNotIn('avg(', query.lower())
        self.assertNotIn('predictions', query)

    def test_empty_tables_return_no_fallback(self):
        self.cursor.description = []
        self.cursor.fetchall.return_value = []
        self.assertEqual(self.client.get('/leaderboard').json()['models'], [])
        self.assertEqual(self.client.get('/performance-history?model=missing&seed=1').status_code, 404)
        payload = self.client.get('/bess/2026-08-31').json()
        self.assertEqual(payload['plan'], [])
        self.assertEqual(payload['results'], [])
        queries = self.cursor.execute.call_args_list[-2:]
        for call in queries:
            self.assertEqual(call.args[1], (date(2026, 8, 31),))
            self.assertNotIn('predictions', call.args[0])

    def test_battery_reads_stored_values_without_recomputing(self):
        def execute(query, parameters):
            if 'FROM bess_plan' in query:
                self.cursor.description = [('model',), ('carga_mw',), ('soc_mwh',), ('simulador',)]
                self.cursor.fetchall.return_value = [('custom', 3.5, 7.0, {'eficiencia': 0.82})]
            else:
                self.cursor.description = [('model',), ('captura_pct',), ('ingreso_eur',)]
                self.cursor.fetchall.return_value = [('custom', 0.0, -12.5)]
        self.cursor.execute.side_effect = execute
        data = self.client.get('/bess/2026-08-31').json()
        self.assertEqual(data['plan'][0]['carga_mw'], 3.5)
        self.assertEqual(data['plan'][0]['simulador']['eficiencia'], 0.82)
        self.assertEqual(data['results'][0]['captura_pct'], 0)
        self.assertEqual(data['results'][0]['ingreso_eur'], -12.5)

    def test_db_failure_is_unavailable_not_a_simulated_result(self):
        self.cursor.execute.side_effect = RuntimeError('private credentials')
        for path in ('/leaderboard', '/performance-history?model=gru&seed=44', '/bess/2026-08-31'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private', response.text)

    def test_nonfinite_numbers_remain_missing(self):
        self.assertEqual(clean({'mae': float('nan'), 'nested': [float('inf'), 0]}), {'mae': None, 'nested': [None, 0]})

    def test_performance_skill_aggregates_maes_instead_of_daily_percentages(self):
        rows = [
            {'date': date(2026, 8, 30), 'n_obs': 24, 'mae': 18.52, 'mae_naive': 5.56},
            {'date': date(2026, 8, 31), 'n_obs': 24, 'mae': 28.18, 'mae_naive': 20.11},
        ]
        expected = 100 * (1 - (18.52 + 28.18) / (5.56 + 20.11))
        self.assertAlmostEqual(weighted_skill(rows), expected)
        self.assertNotAlmostEqual(weighted_skill(rows), (-233.2 - 40.1) / 2)

    def test_performance_summary_keeps_calendar_window_and_common_observations(self):
        rows = [
            {'date': date(2026, 8, 3), 'n_obs': 24, 'mae': 10.0, 'mae_naive': 20.0},
            {'date': date(2026, 8, 17), 'n_obs': 23, 'mae': 30.0, 'mae_naive': 20.0},
            {'date': date(2026, 8, 18), 'n_obs': 24, 'mae': 5.0, 'mae_naive': 10.0},
            {'date': date(2026, 9, 1), 'n_obs': 24, 'mae': 8.0, 'mae_naive': 10.0},
        ]
        result = performance_summary(rows, 30, date(2026, 8, 3), date(2026, 9, 1))
        self.assertEqual(result['evaluated_days'], 4)
        self.assertEqual(result['observations'], 95)
        self.assertEqual(result['days_won'], 3)
        self.assertEqual(result['recent_evaluated_days'], 1)
        self.assertAlmostEqual(result['recent_skill_pct'], 20.0)

    def test_legacy_filters_do_not_silently_relabel_stored_results(self):
        for path in ('/leaderboard?source=production&days=30', '/bess/2026-08-31?duration=4'):
            self.assertEqual(self.client.get(path).status_code, 400)
        self.cursor.execute.assert_not_called()


class ConnectionTests(unittest.TestCase):
    def test_connections_are_read_only_and_closed_even_on_error(self):
        environment = {'DASHBOARD_DB_MODE': 'environment', 'PGHOST': 'test',
                       'PGDATABASE': 'test', 'PGUSER': 'test', 'PGPASSWORD': 'test'}
        for fail in (False, True):
            connection = MagicMock()
            with patch.dict('os.environ', environment), patch('psycopg2.connect', return_value=connection) as connect:
                try:
                    with _connection() as active:
                        self.assertIs(active, connection)
                        if fail:
                            raise ValueError('test')
                except ValueError:
                    pass
                connection.close.assert_called_once()
                self.assertIn('default_transaction_read_only=on', connect.call_args.kwargs['options'])


if __name__ == '__main__':
    unittest.main()
