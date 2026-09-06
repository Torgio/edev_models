import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from api.dashboard_api import app
from api.peak_accuracy import evaluation_window, hour_slots, peak_accuracy


def day_rows(day, predicted_index=12, actual_index=13):
    return [(slot, 100.0 if index == predicted_index else 0.0,
             100.0 if index == actual_index else 0.0)
            for index, slot in enumerate(hour_slots(day))]


class PeakTests(unittest.TestCase):
    def test_postgres_numeric_prices_remain_evaluable(self):
        day = date(2026, 8, 20)
        rows = [(t, p, Decimal(str(a))) for t, p, a in day_rows(day)]
        self.assertEqual(peak_accuracy(rows, day, day, "ensemble")["hits"], 1)
        for value in (Decimal("NaN"), Decimal("Infinity")):
            invalid = [(rows[0][0], rows[0][1], value)] + rows[1:]
            self.assertEqual(peak_accuracy(invalid, day, day, "ensemble")["evaluated_days"], 0)

    def test_one_hour_hits_and_two_hours_misses(self):
        day = date(2026, 8, 20)
        for actual, hits in ((11, 1), (12, 1), (13, 1), (14, 0)):
            result = peak_accuracy(day_rows(day, 12, actual), day, day, "ensemble")
            self.assertEqual(result["hits"], hits)
            self.assertEqual(result["evaluated_days"], 1)

    def test_incomplete_and_missing_days_are_excluded_not_misses(self):
        day = date(2026, 8, 20)
        rows = day_rows(day) + day_rows(day + timedelta(days=1))[:-1]
        result = peak_accuracy(rows, day, day + timedelta(days=2), "ensemble")
        self.assertEqual((result["hits"], result["evaluated_days"], result["excluded_days"]), (1, 1, 2))

    def test_missing_nonfinite_and_duplicate_samples_exclude_day(self):
        day = date(2026, 8, 20)
        for value in (None, float("nan"), float("inf")):
            rows = day_rows(day)
            rows[0] = (rows[0][0], value, 0.0)
            self.assertEqual(peak_accuracy(rows, day, day, "ensemble")["evaluated_days"], 0)
            rows[0] = (rows[0][0], 0.0, value)
            self.assertEqual(peak_accuracy(rows, day, day, "ensemble")["evaluated_days"], 0)
        rows = day_rows(day)
        self.assertEqual(peak_accuracy(rows + [rows[0]], day, day, "ensemble")["evaluated_days"], 0)

    def test_dst_complete_days_and_elapsed_time(self):
        for day, length in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
            self.assertEqual(len(hour_slots(day)), length)
            result = peak_accuracy(day_rows(day, 1, 2), day, day, "ensemble")
            self.assertEqual(result["evaluated_days"], 1)
            self.assertEqual(result["hits"], 1)
        # 01:00 CEST to the second 02:00 (CET) is two elapsed hours, not one.
        day = date(2026, 10, 25)
        self.assertEqual(peak_accuracy(day_rows(day, 1, 3), day, day, "ensemble")["hits"], 0)

    def test_ties_do_not_choose_prediction_using_real_outcome(self):
        day = date(2026, 8, 20)
        rows = day_rows(day, 2, 15)
        rows[15] = (rows[15][0], 100.0, 100.0)
        self.assertEqual(peak_accuracy(rows, day, day, "ensemble")["hits"], 0)
        rows[3] = (rows[3][0], 0.0, 100.0)
        result = peak_accuracy(rows, day, day, "ensemble")
        self.assertEqual(result["hits"], 1)
        self.assertEqual(len(result["days"][0]["actual_peaks"]), 2)

    def test_midnight_is_not_circular(self):
        day = date(2026, 8, 20)
        self.assertEqual(peak_accuracy(day_rows(day, 0, 23), day, day, "ensemble")["hits"], 0)

    def test_window_excludes_today_and_future_without_searching_for_30_valid_days(self):
        today = date(2026, 8, 31)
        for requested in (None, today, date(2026, 9, 5)):
            self.assertEqual(evaluation_window(requested, 30, today), (date(2026, 8, 1), date(2026, 8, 30)))
        self.assertEqual(evaluation_window(date(2026, 8, 20), 30, today), (date(2026, 7, 22), date(2026, 8, 20)))

    def test_endpoint_reads_only_requested_production_model_and_window(self):
        with patch("api.dashboard_api.auth_config", return_value=None), patch("api.dashboard_api._connection") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = day_rows(date(2026, 8, 20))
            response = TestClient(app).get("/peak-accuracy?end_date=2026-08-20&days=1&model=ensemble")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["hits"], 1)
            query, parameters = cursor.execute.call_args.args
            self.assertIn("p.source = %s", query)
            self.assertEqual(parameters[:2], ("ensemble", "production"))
            self.assertEqual(parameters[2], datetime(2026, 8, 19, 22, tzinfo=timezone.utc))
            self.assertEqual(parameters[3], datetime(2026, 8, 20, 22, tzinfo=timezone.utc))

    def test_invalid_filters_and_unavailable_db(self):
        with patch("api.dashboard_api.auth_config", return_value=None), patch("api.dashboard_api._connection") as connect:
            client = TestClient(app)
            for query in ("days=0", "days=31", "source=test", "end_date=bad", "end_date=0001-01-01"):
                self.assertEqual(client.get("/peak-accuracy?" + query).status_code, 422)
            connect.assert_not_called()
            connect.side_effect = RuntimeError("private connection details")
            response = client.get("/peak-accuracy")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private", response.text)


if __name__ == "__main__":
    unittest.main()
