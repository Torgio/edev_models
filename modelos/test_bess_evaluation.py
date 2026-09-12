"""Regresiones fisicas y temporales; sin acceso a PostgreSQL."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from bess_evaluation import Battery, SIMULADOR, optimizar, valorar, validar_plan, ciclos
from tiempo_mercado import periodos_dia, dia_completo, naive_local, serie_local
from evaluar_modelos import arbitraje, de_largo
from evaluar_diario import dia_de, evaluar_dias, ventana
from test_serie_diaria import ConexionFalsa, marco


class PhysicalBatteryTests(unittest.TestCase):
    def test_cannot_sell_before_first_charge(self):
        plan = optimizar([200, 0, 0])
        self.assertAlmostEqual(plan['descarga'][0], 0)
        self.assertAlmostEqual(valorar(plan, [200, 0, 0]).sum(), 0)
        self.assertGreaterEqual(plan['soc'].min(), -1e-7)

    def test_round_trip_and_power_are_physical(self):
        p = [0, 0, 0, 100, 100, 100]
        plan = optimizar(p)
        validar_plan(plan)
        self.assertAlmostEqual(plan['descarga'].sum()/plan['carga'].sum(), .9)
        self.assertAlmostEqual(plan['soc'][-1], 0)
        self.assertAlmostEqual(ciclos(plan), 1)
        self.assertAlmostEqual(valorar(plan, p).sum(), 2*np.sqrt(.9)*100)

    def test_negative_prices_never_charge_and_discharge_simultaneously(self):
        plan = optimizar([-100]*8)
        self.assertFalse(np.any((plan['carga'] > 1e-6) & (plan['descarga'] > 1e-6)))
        validar_plan(plan)

    def test_flat_positive_prices_allow_no_operation(self):
        plan = optimizar([50]*24)
        self.assertAlmostEqual(ciclos(plan), 0)
        self.assertAlmostEqual(valorar(plan, [50]*24).sum(), 0)

    def test_quarter_hour_energy_not_mw_sum(self):
        bat = Battery(eficiencia=1, paso_h=.25)
        plan = optimizar([0, 100], bat)
        self.assertAlmostEqual(valorar(plan, [0, 100], bat).sum(), 25)
        self.assertAlmostEqual(plan['soc'][0], .25)

    def test_discharge_cost_changes_dispatch(self):
        bat = Battery(coste_descarga_eur_mwh=200)
        plan = optimizar([0, 100], bat)
        self.assertAlmostEqual(ciclos(plan, bat), 0)

    def test_oracle_dominates_fixed_plan(self):
        pred = np.array([0, 5, 20, 100, 30, 10])
        real = pred[::-1]
        chosen = optimizar(pred)
        oracle = optimizar(real)
        self.assertGreaterEqual(valorar(oracle, real).sum() + 1e-6, valorar(chosen, real).sum())
        # Real prices do not modify the selected dispatch.
        before = chosen['carga'].copy()
        valorar(chosen, real)
        np.testing.assert_array_equal(before, chosen['carga'])

    def test_reject_invalid_inputs_and_unphysical_plan(self):
        for values in ([], [1, np.nan], [np.inf], [[1, 2]]):
            with self.assertRaises(ValueError):
                optimizar(values)
        with self.assertRaises(ValueError):
            valorar(dict(carga=[0], descarga=[1], soc=[-1]), [100])


class MarketTimeTests(unittest.TestCase):
    def test_complete_days_and_missing_hour(self):
        for day, count in [('2026-03-29', 23), ('2026-06-01', 24), ('2026-10-25', 25)]:
            idx = periodos_dia(day)
            self.assertEqual(len(idx), count)
            self.assertTrue(dia_completo(idx))
            self.assertTrue(dia_completo(idx.tz_convert('UTC')))
            self.assertFalse(dia_completo(idx[:-1]))
            self.assertFalse(dia_completo(idx[:-1].append(idx[:1])))
            self.assertEqual(len(periodos_dia(day, 15)), 4*count)

    def test_october_real_preserves_both_hours_baseline_uses_first(self):
        idx = periodos_dia('2026-10-25')
        real = pd.Series(np.arange(25.), index=idx)
        self.assertEqual(len(serie_local(real)), 25)
        nxt = naive_local(real, periodos_dia('2026-10-26'))
        self.assertEqual(nxt.iloc[2], 2)
        self.assertEqual(nxt.iloc[3], 4)

    def test_march_missing_baseline_is_not_shifted_or_imputed(self):
        idx = periodos_dia('2026-03-29')
        real = pd.Series(np.arange(23.), index=idx)
        nxt = naive_local(real, periodos_dia('2026-03-30'))
        self.assertTrue(np.isnan(nxt.iloc[2]))
        self.assertEqual(nxt.iloc[3], 2)

    def test_legacy_single_ambiguous_hour_does_not_become_complete(self):
        idx = pd.date_range('2026-10-25', periods=24, freq='h')
        self.assertFalse(dia_completo(idx))
        self.assertEqual(len(serie_local(pd.Series(1., index=idx))), 23)

    def test_long_reader_does_not_drop_october_hour(self):
        idx = periodos_dia('2026-10-25').tz_convert('UTC')
        source = pd.DataFrame({'datetime_utc': idx.astype(str), 'precio_pred': np.arange(25.)})
        with patch('evaluar_modelos.pd.read_csv', return_value=source):
            out = de_largo('unused.csv')
        self.assertEqual(len(out), 25)
        self.assertFalse(out.index.has_duplicates)

    def test_bess_evaluates_23_and_25_hours_and_rejects_gaps(self):
        for day in ['2026-03-29', '2026-10-25']:
            idx = periodos_dia(day)
            p = np.linspace(0, 100, len(idx))
            g = marco(idx, p, p)
            result = dia_de(g, p)
            self.assertAlmostEqual(result['captura_pct'], 100)
            self.assertIsNone(dia_de(g.iloc[:-1], p[:-1]))
            capture, income = arbitraje(pd.Series(p, index=idx), pd.Series(p, index=idx))
            self.assertAlmostEqual(capture, result['captura_pct'])
            self.assertAlmostEqual(income, result['ingreso_eur'])


class PersistenceTests(unittest.TestCase):
    def test_settle_saved_dispatch_not_new_forecast(self):
        idx = periodos_dia('2026-06-02')
        prices = np.r_[np.zeros(12), np.full(12, 100.)]
        g = marco(idx, prices, prices)
        # The recorded decision was to wait; fresh forecast would earn money.
        saved = pd.DataFrame({'ts': idx, 'model': 'm', 'carga_mw': 0., 'descarga_mw': 0.,
                              'soc_mwh': 0., 'simulador': [SIMULADOR]*24})
        con = ConexionFalsa()
        real = pd.concat([pd.Series(prices, index=periodos_dia('2026-06-01')),
                          pd.Series(prices, index=idx)])
        with patch('evaluar_diario.cargar_planes', return_value=saved):
            evaluar_dias(con, g, real, True)
        self.assertEqual(len(con.filas), 1)
        self.assertEqual(con.filas[0][2], 0)
        self.assertGreater(con.filas[0][3], 0)
        self.assertEqual(con.filas[0][6], 0)
        # A valid saved plan remains evaluable if predictions was deleted or overwritten late.
        con2 = ConexionFalsa()
        with patch('evaluar_diario.cargar_planes', return_value=saved):
            evaluar_dias(con2, pd.DataFrame(), real, True, date(2026, 6, 2), date(2026, 6, 2))
        self.assertEqual(con2.filas, con.filas)

    def test_old_or_incomplete_plan_is_not_liquidated_as_v2(self):
        idx = periodos_dia('2026-06-02')
        g = marco(idx, np.arange(24.), np.arange(24.))
        for metadata, length in [({}, 24), (SIMULADOR, 23)]:
            saved = pd.DataFrame({'ts': idx[:length], 'model': 'm', 'carga_mw': 0.,
                                  'descarga_mw': 0., 'soc_mwh': 0., 'simulador': [metadata]*length})
            con = ConexionFalsa()
            with patch('evaluar_diario.cargar_planes', return_value=saved):
                self.assertEqual(evaluar_dias(con, g, pd.Series(dtype=float), True), 0)
            self.assertEqual(con.filas, [])

    def test_rolling_ranking_uses_exact_common_instants(self):
        idx = periodos_dia('2026-06-02')
        first = marco(idx, np.arange(24.)+2, np.arange(24.))
        second = first.copy().assign(model='other')
        # Equal counts are not enough: different missing hours must intersect.
        data = pd.concat([first.iloc[1:], second.iloc[:-1]])
        real = pd.Series(np.arange(24.)-1, index=periodos_dia('2026-06-01'))
        con = ConexionFalsa()
        ventana(con, data, real, True)
        self.assertEqual(len(con.filas), 3)
        self.assertEqual({row[4] for row in con.filas}, {22})
        self.assertEqual({row[5] for row in con.filas if row[0] != 'naive_D1'}, {2.})
        self.assertTrue(all(row[10] is None for row in con.filas))  # no complete BESS day

class PlannerTests(unittest.TestCase):
    def connection(self, idx):
        class Connection:
            writes = None
            def cursor(con):
                class Cursor:
                    def __enter__(self): return self
                    def __exit__(self, *args): return False
                    def execute(self, *args): pass
                    def fetchall(self): return list(zip(idx, np.linspace(0, 100, len(idx))))
                    def executemany(self, sql, rows): con.writes = rows
                return Cursor()
            def commit(self): pass
        return Connection()

    def test_planner_persists_physical_dispatch_for_dst_days(self):
        import run_diario
        from tiempo_mercado import cierre_prediccion
        for day in (date(2026, 3, 29), date(2026, 10, 25)):
            idx = periodos_dia(day)
            con = self.connection(idx)
            with patch('run_diario._ahora', return_value=cierre_prediccion(day)-pd.Timedelta(minutes=30)):
                self.assertEqual(run_diario.planificar(con, day, 'm'), len(idx))
            stored = np.array([row[2:6] for row in con.writes])
            dispatch = dict(carga=stored[:, 0], descarga=stored[:, 1], soc=stored[:, 2])
            validar_plan(dispatch)
            np.testing.assert_allclose(stored[:, 3], valorar(dispatch, np.linspace(0, 100, len(idx))))

    def test_planner_does_not_rewrite_after_cutoff_or_on_gaps(self):
        import run_diario
        from tiempo_mercado import cierre_prediccion
        day = date(2026, 6, 2)
        for idx, now in [(periodos_dia(day), cierre_prediccion(day)),
                         (periodos_dia(day)[:-1], cierre_prediccion(day)-pd.Timedelta(hours=1))]:
            con = self.connection(idx)
            with patch('run_diario._ahora', return_value=now):
                self.assertEqual(run_diario.planificar(con, day, 'm'), 0)
            self.assertIsNone(con.writes)

    def test_solver_crossing_cutoff_does_not_write(self):
        import run_diario
        from tiempo_mercado import cierre_prediccion
        day = date(2026, 6, 2)
        con = self.connection(periodos_dia(day))
        cutoff = cierre_prediccion(day)
        with patch('run_diario._ahora', side_effect=[cutoff-pd.Timedelta(seconds=1), cutoff]):
            self.assertEqual(run_diario.planificar(con, day, 'm'), 0)
        self.assertIsNone(con.writes)


class MetadataTests(unittest.TestCase):
    def test_same_count_different_instants_have_different_fingerprint(self):
        from evaluar_modelos import metadata_evaluacion
        first = periodos_dia('2026-06-01')
        second = periodos_dia('2026-06-02')
        self.assertNotEqual(metadata_evaluacion(first), metadata_evaluacion(second))
        self.assertEqual(metadata_evaluacion(first), metadata_evaluacion(first.tz_convert('UTC')))

    def test_csv_registration_preserves_new_metadata_and_legacy_definition(self):
        import json
        import tempfile
        import registrar_modelos as register
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'data_temp').mkdir()
            spec = {**SIMULADOR, 'muestra_sha256': 'sample'}
            pd.DataFrame([{'modelo': 'm', 'n_horas': 24, 'simulador': json.dumps(spec)}]).to_csv(
                root / 'data_temp/leaderboard_validation.csv', index=False)
            pd.DataFrame([{'modelo': 'm', 'n_horas': 24}]).to_csv(
                root / 'data_temp/leaderboard_test.csv', index=False)
            with patch.object(register, 'REPO', root):
                rows = register.metricas()
            self.assertEqual(json.loads(rows[0]['simulador']), spec)
            self.assertNotIn('version', json.loads(rows[1]['simulador']))


if __name__ == '__main__':
    unittest.main()
