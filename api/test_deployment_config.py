"""Pruebas de configuración sin conectar a una base de datos real."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from api.dashboard_api import _connection


class DeploymentConfigTests(unittest.TestCase):
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
