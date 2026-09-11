import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import date
import numpy as np
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent))
import verificar_bess_v2 as verifier
from tiempo_mercado import periodos_dia
from bess_evaluation import SIMULADOR

class VerifyTests(unittest.TestCase):
    def fixture(self):
        day=date(2026,9,11)
        idx=periodos_dia(day)
        saved=pd.DataFrame({'ts':idx,'model':'m','carga_mw':0.,'descarga_mw':0.,
                            'soc_mwh':0.,'simulador':[SIMULADOR]*24,
                            'updated_at':pd.Timestamp('2026-09-10T09:35Z')})
        real=pd.Series(50.,index=periodos_dia('2026-09-10').append(idx))
        result=pd.DataFrame([{'model':'m','simulador':SIMULADOR,'ingreso_eur':0.,
                             'ingreso_oraculo_eur':0.,'ingreso_naive_eur':0.,
                             'captura_pct':None,'ciclos':0.}])
        class Con:
            def cursor(self):
                class Cur:
                    def __enter__(self):return self
                    def __exit__(self,*args):return False
                return Cur()
        return day,saved,real,result,Con()

    def test_matching_record_passes(self):
        day,saved,real,result,con=self.fixture()
        with patch.object(verifier.pd,'read_sql',side_effect=[saved,result]),patch.object(verifier,'curva_real',return_value=real):
            self.assertEqual(verifier.verificar(con,day),0)

    def test_wrong_income_fails(self):
        day,saved,real,result,con=self.fixture()
        result['ingreso_eur']=100.
        with patch.object(verifier.pd,'read_sql',side_effect=[saved,result]),patch.object(verifier,'curva_real',return_value=real):
            with self.assertRaises(ValueError):verifier.verificar(con,day)

    def test_missing_result_is_pending(self):
        day,saved,real,result,con=self.fixture()
        with patch.object(verifier.pd,'read_sql',side_effect=[saved,result.iloc[:0]]),patch.object(verifier,'curva_real',return_value=real):
            self.assertEqual(verifier.verificar(con,day),3)

    def test_negative_soc_fails(self):
        day,saved,real,result,con=self.fixture()
        saved.loc[0,'soc_mwh']=-1.
        with patch.object(verifier.pd,'read_sql',side_effect=[saved,result]),patch.object(verifier,'curva_real',return_value=real):
            with self.assertRaises(ValueError):verifier.verificar(con,day)

if __name__=='__main__':unittest.main()
