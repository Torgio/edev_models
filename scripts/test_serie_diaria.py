"""La serie diaria del error: coberturas distintas y los dos domingos del cambio de hora.

Estas dos cosas no se pueden comprobar mirando agosto -- agosto no tiene ni huecos ni
cambio de hora. Se comprueban aqui, con dias fabricados, para que marzo y octubre no
sorprendan a nadie.

    python -m unittest scripts.test_serie_diaria -v
"""
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
for p in ("scripts", "modelos", "ingesta"):
    sys.path.insert(0, str(REPO / p))

from evaluar_diario import NAIVE_REGLA, serie_diaria      # noqa: E402


class ConexionFalsa:
    """No toca la base: recoge lo que se le habria escrito."""
    def __init__(self):
        self.filas = []

    def cursor(self):
        con = self

        class Cur:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def executemany(self_, sql, filas): con.filas.extend(filas)
        return Cur()

    def commit(self):
        pass


def horas(dia, n=24, inicio=0):
    return pd.date_range(f"{dia} {inicio:02d}:00", periods=n, freq="h")


def marco(idx, pred, real):
    return pd.DataFrame({"model": "m", "seed": 44, "ts": idx, "pred": pred, "real": real})


class SerieDiariaTests(unittest.TestCase):

    def _escribir(self, datos, real):
        con = ConexionFalsa()
        serie_diaria(con, datos, real, escribir=True)
        return {f[0]: f for f in con.filas}      # por fecha

    def test_los_dos_mae_usan_las_mismas_horas(self):
        """Si al naive le falta una hora, esa hora tampoco cuenta para el MAE del modelo.

        El dia 2 tiene 24 horas de prediccion, pero el precio del dia 1 solo cubre 23:
        la hora sin naive no puede entrar en el numerador y quedarse fuera del
        denominador, o el skill compararia dos coberturas distintas.
        """
        idx1, idx2 = horas("2026-06-01"), horas("2026-06-02")
        # el dia 1 pierde su ultima hora -> el dia 2 se queda sin naive a las 23:00
        real = pd.Series([50.0] * 23, index=idx1[:23])
        real = pd.concat([real, pd.Series([60.0] * 24, index=idx2)])
        datos = marco(idx2, [70.0] * 23 + [1000.0], [60.0] * 24)   # la hora huerfana miente

        fila = self._escribir(datos, real)[date(2026, 6, 2)]
        _, _, _, _, n, mae, mae_naive, skill, estado, _ = fila
        self.assertEqual(n, 23, "n_obs son las horas COMPARABLES")
        self.assertAlmostEqual(mae, 10.0, places=6,
                               msg="la hora sin naive no debe entrar en el MAE del modelo")
        self.assertAlmostEqual(mae_naive, 10.0, places=6)
        self.assertAlmostEqual(skill, 0.0, places=6)
        self.assertEqual(estado, "horas_incompletas")

    def test_el_primer_dia_no_tiene_ayer(self):
        idx = horas("2026-06-01")
        real = pd.Series([50.0] * 24, index=idx)
        fila = self._escribir(marco(idx, [55.0] * 24, [50.0] * 24), real)[date(2026, 6, 1)]
        self.assertEqual(fila[8], "sin_naive")
        self.assertIsNone(fila[7], "sin ayer no hay skill que calcular")

    def test_marzo_23_horas_queda_marcado_y_sigue_siendo_medible(self):
        """El domingo de marzo la 02:00 no existe: 23 horas.

        El MAE de ese dia es perfectamente comparable con el de cualquier otro -- lo que
        no lo es es su dinero, que necesita 24 h para cerrar el ciclo. Por eso se guarda
        marcado en vez de descartarse.
        """
        idx1 = horas("2026-03-28")
        idx2 = horas("2026-03-29", n=23)
        real = pd.concat([pd.Series([40.0] * 24, index=idx1),
                          pd.Series([44.0] * 23, index=idx2)])
        fila = self._escribir(marco(idx2, [48.0] * 23, [44.0] * 23), real)[date(2026, 3, 29)]
        self.assertEqual(fila[4], 23)
        self.assertEqual(fila[8], "cambio_hora")
        self.assertIsNotNone(fila[5], "un dia de 23 h sigue teniendo MAE")

    def test_octubre_conserva_LAS_DOS_02_00(self):
        """El domingo de octubre tiene 25 horas y las 25 tienen que contar.

        Las dos 02:00 son instantes distintos que comparten etiqueta local. `cargar()` une
        por `datetime` (timestamptz), asi que llegan las dos. Si alguien "arreglara" el
        duplicado descartando una -- la tentacion es grande, porque un indice repetido
        rompe `reindex` en otros sitios -- se perderia una hora real de mercado y el
        conteo no lo delataria: 24 filas un domingo de octubre parecen un dia normal.

        Por eso las dos horas repetidas llevan aqui errores MUY distintos: si una se cae,
        el MAE cambia y este test se entera.
        """
        idx1 = horas("2026-10-24")
        idx2 = pd.to_datetime(
            ["2026-10-25 00:00", "2026-10-25 01:00",
             "2026-10-25 02:00", "2026-10-25 02:00"]          # la repetida, dos veces
            + [f"2026-10-25 {h:02d}:00" for h in range(3, 24)])
        self.assertEqual(len(idx2), 25)
        real_ayer = pd.Series([40.0] * 24, index=idx1)        # ya deduplicado, como curva_real

        pred = [48.0, 48.0, 44.0, 64.0] + [48.0] * 21         # errores 4, 4, 0, 20, luego 4
        datos = marco(idx2, pred, [44.0] * 25)
        fila = self._escribir(datos, real_ayer)[date(2026, 10, 25)]

        self.assertEqual(fila[4], 25, "las dos 02:00 cuentan: son instantes distintos")
        self.assertAlmostEqual(fila[5], (23 * 4 + 0 + 20) / 25, places=6,
                               msg="el MAE tiene que incluir las dos horas repetidas")
        self.assertAlmostEqual(fila[6], 4.0, places=6, msg="naive: 44 real contra 40 de ayer")
        self.assertEqual(fila[8], "cambio_hora")

    def test_un_dia_normal_al_que_le_falta_una_hora_no_es_cambio_de_hora(self):
        """23 filas en junio es un hueco, no un cambio de hora. Confundirlos oculta el hueco."""
        idx1, idx2 = horas("2026-06-10"), horas("2026-06-11")
        real = pd.concat([pd.Series([40.0] * 24, index=idx1),
                          pd.Series([44.0] * 24, index=idx2)])
        datos = marco(idx2[:23], [48.0] * 23, [44.0] * 23)
        fila = self._escribir(datos, real)[date(2026, 6, 11)]
        self.assertEqual(fila[8], "horas_incompletas")

    def test_naive_perfecto_no_es_lo_mismo_que_sin_naive(self):
        """Si el precio no se movio de un dia para otro, mae_naive vale 0.

        El skill queda sin definir --no se divide entre cero-- pero el dia es
        perfectamente valido y tiene que contar. Marcarlo `sin_naive` diria que no hubo
        persistencia, y lo que paso es que fue perfecta: son cosas distintas, y la vista
        solo excluye la primera.
        """
        idx1, idx2 = horas("2026-06-15"), horas("2026-06-16")
        real = pd.concat([pd.Series([50.0] * 24, index=idx1),
                          pd.Series([50.0] * 24, index=idx2)])
        fila = self._escribir(marco(idx2, [53.0] * 24, [50.0] * 24), real)[date(2026, 6, 16)]
        self.assertEqual(fila[8], "naive_perfecto")
        self.assertEqual(fila[6], 0.0, "el naive existio y acerto: mae_naive es 0, no nulo")
        self.assertIsNone(fila[7], "no hay skill que medir, pero el dia cuenta")
        self.assertAlmostEqual(fila[5], 3.0, places=6)

    def test_el_dia_siguiente_al_cambio_de_hora_tambien_queda_marcado(self):
        """El 26 de octubre es un dia normal, pero su naive no lo es.

        Ayer tuvo dos 02:00 y solo se pudo usar una, asi que la persistencia de esa hora
        esta medida contra una de las dos. El dia sale bien en todo lo demas: por eso hay
        que decirlo, o pasaria por `ok`.
        """
        idx1, idx2 = horas("2026-10-25"), horas("2026-10-26")
        real = pd.concat([pd.Series([40.0] * 24, index=idx1),
                          pd.Series([44.0] * 24, index=idx2)])
        fila = self._escribir(marco(idx2, [48.0] * 24, [44.0] * 24), real)[date(2026, 10, 26)]
        self.assertEqual(fila[8], "ayer_cambio_hora")
        self.assertEqual(fila[4], 24, "el dia en si esta completo")

    def test_marzo_29_el_dia_de_despues_no_pasa_por_normal(self):
        """El 30 de marzo hereda el problema al reves: ayer no tuvo 02:00."""
        idx1, idx2 = horas("2026-03-29", n=23), horas("2026-03-30")
        real = pd.concat([pd.Series([40.0] * 23, index=idx1),
                          pd.Series([44.0] * 24, index=idx2)])
        fila = self._escribir(marco(idx2, [48.0] * 24, [44.0] * 24), real)[date(2026, 3, 30)]
        self.assertEqual(fila[8], "ayer_cambio_hora")

    def test_la_regla_guardada_no_promete_24_horas_exactas(self):
        """La regla viaja con cada fila; si dice lo que no hace, es peor que no decir nada.

        `curva_real()` indexa en hora peninsular y el naive es `indice + 1 dia`, o sea la
        MISMA HORA LOCAL de ayer. Los dos domingos del cambio de hora eso no son 24 h.
        """
        self.assertIn("MISMA HORA LOCAL", NAIVE_REGLA)
        self.assertNotIn("absoluto", NAIVE_REGLA)
        self.assertIn("cambio_hora", NAIVE_REGLA)


if __name__ == "__main__":
    unittest.main()
