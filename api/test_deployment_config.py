"""Pruebas de configuración sin conectar a una base de datos real."""
import os
import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.dashboard_api import _connection, day_coverage


class DeploymentConfigTests(unittest.TestCase):
    def test_day_coverage_respects_madrid_daylight_saving_time(self):
        for day, expected in (
            (date(2026, 3, 29), 23),
            (date(2026, 8, 31), 24),
            (date(2026, 10, 25), 25),
        ):
            summary = day_coverage(day, 3, expected * 3, expected * 3, expected, expected,
                                   latest_target=date(2026, 12, 1))
            self.assertEqual(summary["expected_hours"], expected)
            self.assertTrue(summary["closed"])

    def test_partial_real_coverage_is_not_closed(self):
        day = date(2026, 8, 31)
        self.assertFalse(day_coverage(day, 3, 72, 69, 24, 23)["closed"])

    def test_latest_day_ahead_horizon_remains_a_plan_even_with_spot_price(self):
        horizon = date(2026, 9, 2)
        self.assertFalse(day_coverage(horizon, 3, 72, 72, 24, 24,
                                      latest_target=horizon)["closed"])

    @patch.dict(os.environ, {
        "DASHBOARD_DB_MODE": "environment",
        "PGHOST": "127.0.0.1",
        "PGDATABASE": "test_database",
        "PGUSER": "test_reader",
        "PGPASSWORD": "test_password",
    }, clear=True)
    @patch("psycopg2.connect")
    def test_server_configuration(self, connect):
        with _connection() as connection:
            self.assertIs(connection, connect.return_value)
        connect.return_value.close.assert_called_once()
        options = connect.call_args.kwargs
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], 5432)
        self.assertEqual(options["dbname"], "test_database")
        self.assertEqual(options["connect_timeout"], 10)
        self.assertIn("default_transaction_read_only=on", options["options"])
        self.assertIn("statement_timeout=15000", options["options"])

    @patch.dict(os.environ, {"DASHBOARD_DB_MODE": "environment"}, clear=True)
    @patch("psycopg2.connect")
    def test_missing_configuration_does_not_fall_back(self, connect):
        with self.assertRaisesRegex(RuntimeError, "PGDATABASE"):
            with _connection():
                self.fail("Missing credentials must not open a connection")
        connect.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    @patch("psycopg2.connect")
    def test_local_configuration_is_preserved(self, connect):
        config = SimpleNamespace(load_config=Mock(return_value=({}, {"dbname": "local_test"})))
        with patch.dict(sys.modules, {"config": config}):
            with _connection():
                pass
        config.load_config.assert_called_once_with()
        self.assertEqual(connect.call_args.kwargs["dbname"], "local_test")


if __name__ == "__main__":
    unittest.main()
