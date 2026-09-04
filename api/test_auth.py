import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from api.auth import (COOKIE_NAME, SESSION_SECONDS, LoginLimiter, TeamAuth,
                      UserAuth, UserCredential, auth_config, new_credentials,
                      new_user_credentials)
from api.dashboard_api import app

PASSWORD = "test-only-password-123456"


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = new_credentials(PASSWORD)
        cls.auth = TeamAuth(data["salt"], data["password_hash"], data["session_secret"])

    def setUp(self):
        self.auth_patch = patch("api.dashboard_api.auth_config", return_value=self.auth)
        self.limit_patch = patch("api.dashboard_api.login_limiter", LoginLimiter())
        self.auth_patch.start()
        self.limit_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.addCleanup(self.limit_patch.stop)
        self.client = TestClient(app, base_url="https://testserver")

    def test_all_data_routes_require_session(self):
        with patch("api.dashboard_api._connection") as connect:
            for path in ("/health", "/days", "/predictions/2026-08-31", "/leaderboard", "/bess/2026-08-31", "/peak-accuracy"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401, path)
                self.assertIn("no-store", response.headers["cache-control"])
            connect.assert_not_called()

    def test_login_session_and_logout(self):
        self.assertFalse(self.client.get("/session").json()["authenticated"])
        response = self.client.post("/login", json={"password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        for flag in ("HttpOnly", "Secure", "SameSite=strict", "Max-Age=28800"):
            self.assertIn(flag, cookie)
        self.assertNotIn(PASSWORD, response.text + cookie)
        self.assertTrue(self.client.get("/session").json()["authenticated"])
        with patch("api.dashboard_api._connection") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (None, 7)
            self.assertEqual(self.client.get("/health").json()["rows"], 7)
        self.assertEqual(self.client.post("/logout").status_code, 200)
        self.assertEqual(self.client.get("/health").status_code, 401)

    def test_wrong_password(self):
        response = self.client.post("/login", json={"password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("set-cookie", response.headers)

    def test_individual_user_login_and_identity(self):
        data = new_user_credentials([
            ("magui", "one-test-password-123"),
            ("equipo2", "two-test-password-456"),
        ])
        users = tuple(UserCredential(item["username"], item["salt"], item["password_hash"], item["enabled"])
                      for item in data["users"])
        multi_auth = UserAuth(users, data["session_secret"])
        with patch("api.dashboard_api.auth_config", return_value=multi_auth):
            client = TestClient(app, base_url="https://testserver")
            response = client.post("/login", json={"username": "MAGUI", "password": "one-test-password-123"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["username"], "magui")
            session = client.get("/session").json()
            self.assertTrue(session["authenticated"])
            self.assertEqual(session["username"], "magui")
            self.assertEqual(client.post("/login", json={"username": "equipo2", "password": "wrong"}).status_code, 401)
            self.assertEqual(client.post("/login", json={"username": "unknown", "password": "one-test-password-123"}).status_code, 401)

    def test_disabled_user_and_password_rotation_invalidate_session(self):
        data = new_user_credentials([("magui", "one-test-password-123")])
        item = data["users"][0]
        enabled = UserAuth((UserCredential(item["username"], item["salt"], item["password_hash"], True),), data["session_secret"])
        token = enabled.issue("magui", now=1000)
        self.assertEqual(enabled.authenticated_user(token, now=1001), "magui")
        disabled = UserAuth((UserCredential(item["username"], item["salt"], item["password_hash"], False),), data["session_secret"])
        self.assertFalse(disabled.valid(token, now=1001))
        rotated_data = new_user_credentials([("magui", "rotated-password-789")])
        rotated_item = rotated_data["users"][0]
        rotated = UserAuth((UserCredential(rotated_item["username"], rotated_item["salt"], rotated_item["password_hash"], True),), data["session_secret"])
        self.assertFalse(rotated.valid(token, now=1001))

    def test_expired_and_tampered_session(self):
        token = self.auth.issue(now=1000)
        self.assertTrue(self.auth.valid(token, now=1001))
        self.assertFalse(self.auth.valid(token, now=1000 + SESSION_SECONDS))
        self.assertFalse(self.auth.valid(token + "0", now=1001))
        self.assertFalse(self.auth.valid("malformed"))
        self.assertFalse(self.auth.valid("ü.invalid"))
        self.assertFalse(self.auth.valid(token, now=500))
        rotated = TeamAuth(self.auth.salt, "0" * 64, self.auth.session_secret)
        self.assertFalse(rotated.valid(token, now=1001))

    def test_fake_cookie_cannot_reach_database(self):
        response = self.client.get("/health", headers={"Cookie": f"{COOKIE_NAME}=fake.token"})
        self.assertEqual(response.status_code, 401)

    def test_cross_origin_login_rejected(self):
        response = self.client.post("/login", json={"password": PASSWORD}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)

    def test_body_and_media_limits(self):
        self.assertEqual(self.client.post("/login", content="x" * 2049, headers={"Content-Type": "application/json"}).status_code, 413)
        self.assertEqual(self.client.post("/login", content="password=secret").status_code, 415)
        self.assertEqual(self.client.post("/login", json={"password": 123}).status_code, 400)

    def test_login_is_rate_limited(self):
        # Formato inválido también consume intentos, sin calcular hashes costosos.
        for _ in range(10):
            self.assertEqual(self.client.post("/login", json={}).status_code, 400)
        response = self.client.post("/login", json={"password": PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")

    def test_limiter_window_and_global_limit(self):
        limiter = LoginLimiter()
        for _ in range(10):
            self.assertTrue(limiter.allow("one", now=0))
        self.assertFalse(limiter.allow("one", now=1))
        self.assertTrue(limiter.allow("one", now=61))
        for index in range(29):
            self.assertTrue(limiter.allow(str(index), now=61))
        self.assertFalse(limiter.allow("other", now=61))

    def test_production_config_fails_closed(self):
        auth_config.cache_clear()
        try:
            with patch.dict(os.environ, {"DASHBOARD_DB_MODE": "environment"}, clear=True):
                with self.assertRaises(RuntimeError):
                    auth_config()
        finally:
            auth_config.cache_clear()


if __name__ == "__main__":
    unittest.main()
