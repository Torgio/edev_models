"""Preflight checks only: no database writes or optimizations."""
import threading
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi import HTTPException
from production.api import bateria


class HorizonTests(unittest.TestCase):
    def test_names_and_unique_default_codes(self):
        first = bateria.Estudio(desde='2027-01-01', hasta='2027-01-02', nombre='Fábrica')
        second = bateria.Estudio(desde='2027-01-01', hasta='2027-01-02', nombre='Fábrica')
        self.assertNotEqual(first.code, second.code)
        self.assertEqual(first.nombre, 'Fábrica')
        with self.assertRaises(ValueError):
            bateria.Estudio(desde='2027-01-01', hasta='2027-01-02', nombre='x' * 121)

    def test_catalog_preserves_missing_snapshot_values(self):
        with patch.object(bateria, 'cursor') as cursor:
            cur = cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = (1,)
            cur.fetchall.return_value = [(7, 'WEB-A', None, 'Fábrica', '0.1', '4', '2027-01-01', '2031-12-31'),
                                        (6, 'WEB-B', None, 'Antiguo', None, None, None, None)]
            rows = bateria.ejecuciones_web()['runs']
            self.assertEqual(rows[0]['power_kw'], 100)
            self.assertEqual(rows[0]['name'], 'Fábrica')
            self.assertIsNone(rows[1]['power_kw'])
            self.assertIsNone(rows[1]['date_from'])

    def test_multiyear_and_busy(self):
        index = pd.DataFrame({"dia": pd.date_range('2027-01-01', '2046-12-31')})
        with patch('production.curva.generar_curva.leer', return_value=(np.zeros((5, 1)), index, {})), \
             patch.object(bateria, 'ESTUDIO_SLOT', threading.BoundedSemaphore(1)), \
             patch.object(bateria, 'TAREAS', {}), \
             patch.object(bateria.threading, 'Thread') as worker:
            study = bateria.Estudio(desde='2027-01-01', hasta='2046-12-31')
            self.assertEqual(bateria.lanzar(study)['estado'], 'corriendo')
            worker.return_value.start.assert_called_once()
            with self.assertRaises(HTTPException) as caught:
                bateria.lanzar(study)
            self.assertEqual(caught.exception.status_code, 429)

    def test_missing_scenarios_never_launch(self):
        index = pd.DataFrame({"dia": pd.date_range('2027-01-01', '2027-12-31')})
        with patch('production.curva.generar_curva.leer', return_value=(np.zeros((5, 1)), index, {})), \
             patch.object(bateria.threading, 'Thread') as worker:
            with self.assertRaises(HTTPException) as caught:
                bateria.lanzar(bateria.Estudio(desde='2027-01-01', hasta='2031-12-31'))
            self.assertEqual(caught.exception.status_code, 400)
            worker.assert_not_called()

    def test_twenty_year_limit(self):
        with self.assertRaises(HTTPException) as caught:
            bateria.lanzar(bateria.Estudio(desde='2027-01-01', hasta='2047-12-31'))
        self.assertEqual(caught.exception.status_code, 400)
