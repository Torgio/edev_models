"""La seleccion se basa solo en disponibilidad y conserva igualdad temporal."""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluar_diario import seleccionar_cohorte, ventana
from test_serie_diaria import marco, ConexionFalsa
from tiempo_mercado import periodos_dia

class CohorteTests(unittest.TestCase):
    def datos(self):
        idx = pd.date_range('2026-09-02', periods=240, freq='h', tz='Europe/Madrid')
        a = marco(idx, np.arange(240.), np.arange(240.))
        return pd.concat([a, a.assign(model='b'), a.iloc[:24].assign(model='retirado')])

    def test_retired_model_does_not_reduce_ten_days_to_one(self):
        chosen, meta = seleccionar_cohorte(self.datos())
        self.assertEqual(set(chosen.model), {'m','b'})
        self.assertEqual(len(chosen), 480)
        self.assertEqual(meta['excluidos'][0]['model'], 'retirado')
        self.assertIn('sin_datos_ultimo_dia', meta['excluidos'][0]['motivos'])

    def test_all_policy_remains_explicitly_available(self):
        chosen, meta = seleccionar_cohorte(self.datos(), 'todos')
        self.assertEqual(set(chosen.model), {'m','b','retirado'})
        self.assertEqual(meta['excluidos'], [])

    def test_no_selection_by_quality(self):
        data = self.datos()
        data.loc[data.model == 'b', 'pred'] = 1e8
        chosen, _ = seleccionar_cohorte(data)
        self.assertIn('b', set(chosen.model))

    def test_nonfinite_values_do_not_inflate_coverage(self):
        data = self.datos()
        data.loc[(data.model == 'b') & (data.ts.dt.day < 10), 'pred'] = np.nan
        chosen, _ = seleccionar_cohorte(data)
        self.assertEqual(set(chosen.model), {'m'})

    def test_threshold_is_inclusive_and_last_day_is_required(self):
        data = self.datos()
        a = data[data.model == 'm']
        chosen, _ = seleccionar_cohorte(pd.concat([a, a.iloc[24:].assign(model='ninety'),
                                                    a.iloc[:-24].assign(model='old')]))
        self.assertEqual(set(chosen.model), {'m','ninety'})

    def test_duplicate_instant_rejected(self):
        data = self.datos()
        with self.assertRaises(ValueError):
            seleccionar_cohorte(pd.concat([data, data.iloc[:1]]))

    def test_rolling_window_clears_stale_global_rows_in_same_transaction(self):
        class Con(ConexionFalsa):
            def __init__(self):
                super().__init__()
                self.updates=[]
            def cursor(self):
                cur=super().cursor()
                cur.execute=lambda sql, params: self.updates.append((sql,params))
                return cur
        con=Con()
        data=self.datos()
        idx = pd.date_range('2026-09-01', periods=264, freq='h', tz='Europe/Madrid')
        real=pd.Series(np.arange(264.), index=idx)
        ventana(con,data,real,True)
        self.assertEqual({r[0] for r in con.filas}, {'m','b','naive_D1'})
        self.assertEqual({r[4] for r in con.filas}, {240})
        self.assertEqual(len(con.updates),1)
        self.assertIn("periodo='prod_30d' AND corte='global'",con.updates[0][0])
        self.assertIn('mae=NULL',con.updates[0][0])

if __name__=='__main__':
    unittest.main()
