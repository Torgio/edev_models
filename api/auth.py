"""Acceso de equipo: hash de contraseña y cookies firmadas, sin datos de PostgreSQL."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock

COOKIE_NAME = "pulso_session"
SESSION_SECONDS = 8 * 60 * 60
ITERATIONS = 600_000


def password_digest(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


def new_credentials(password: str) -> dict:
    if not 16 <= len(password) <= 128:
        raise ValueError("Usa una contraseña de entre 16 y 128 caracteres.")
    salt = secrets.token_hex(32)
    return {"version": 1, "salt": salt, "password_hash": password_digest(password, salt), "session_secret": secrets.token_hex(32)}


@dataclass(frozen=True)
class TeamAuth:
    salt: str
    password_hash: str
    session_secret: str

    def verify_password(self, password: str) -> bool:
        return hmac.compare_digest(password_digest(password, self.salt), self.password_hash)

    def _signature(self, payload: str) -> str:
        # Rotar la contraseña o la clave invalida las sesiones anteriores.
        key = bytes.fromhex(self.session_secret)
        return hmac.new(key, (self.password_hash + ":" + payload).encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self, now: int | None = None) -> str:
        now = int(time.time()) if now is None else now
        body = json.dumps({"iat": now, "exp": now + SESSION_SECONDS, "nonce": secrets.token_hex(16)}, separators=(",", ":"))
        payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return payload + "." + self._signature(payload)

    def valid(self, token: str | None, now: int | None = None) -> bool:
        if not token or len(token) > 1024 or not token.isascii():
            return False
        try:
            payload, signature = token.split(".")
            if not hmac.compare_digest(signature, self._signature(payload)):
                return False
            body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            now = int(time.time()) if now is None else now
            return (type(body["iat"]) is int and type(body["exp"]) is int
                    and body["iat"] <= now + 30 and now < body["exp"]
                    and body["exp"] - body["iat"] == SESSION_SECONDS)
        except (ValueError, KeyError, TypeError, UnicodeError):
            return False


@lru_cache(maxsize=1)
def auth_config() -> TeamAuth | None:
    # En el servidor la autenticación es obligatoria por defecto.
    required = os.getenv("DASHBOARD_REQUIRE_AUTH", "1" if os.getenv("DASHBOARD_DB_MODE") == "environment" else "0")
    if required != "1":
        return None
    credential_dir = os.getenv("CREDENTIALS_DIRECTORY")
    filename = os.getenv("DASHBOARD_AUTH_FILE") or (str(Path(credential_dir) / "team-auth") if credential_dir else "")
    if not filename:
        raise RuntimeError("Falta configurar la credencial team-auth del servicio.")
    try:
        data = json.loads(Path(filename).read_text())
        if data.get("version") != 1:
            raise ValueError("Versión no admitida")
        for key in ("salt", "password_hash", "session_secret"):
            if not isinstance(data[key], str) or len(data[key]) != 64 or len(bytes.fromhex(data[key])) != 32:
                raise ValueError("Formato incorrecto")
        return TeamAuth(data["salt"], data["password_hash"], data["session_secret"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("La credencial de acceso del equipo falta o no es válida.") from exc


class LoginLimiter:
    """Límite por cliente y global; memoria acotada, para el servicio de un worker."""
    def __init__(self):
        self.lock = Lock()
        self.clients = OrderedDict()
        self.total = deque()

    def allow(self, client: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self.lock:
            while self.total and self.total[0] <= now - 60:
                self.total.popleft()
            if len(self.total) >= 30:
                return False
            attempts = self.clients.setdefault(client, deque())
            self.clients.move_to_end(client)
            while len(self.clients) > 512:
                self.clients.popitem(last=False)
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                return False
            attempts.append(now)
            self.total.append(now)
            return True
