"""Acceso de equipo: hash de contraseña y cookies firmadas, sin datos de PostgreSQL."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import re
from collections import OrderedDict, deque
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock

COOKIE_NAME = "pulso_session"
SESSION_SECONDS = 8 * 60 * 60
ITERATIONS = 600_000
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


def password_digest(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


def new_credentials(password: str) -> dict:
    if not 16 <= len(password) <= 128:
        raise ValueError("Usa una contraseña de entre 16 y 128 caracteres.")
    salt = secrets.token_hex(32)
    return {"version": 1, "salt": salt, "password_hash": password_digest(password, salt), "session_secret": secrets.token_hex(32)}


def normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError("El usuario debe tener entre 3 y 32 caracteres: letras, números, punto, guion o guion bajo.")
    return value


def new_user_credentials(entries: list[tuple[str, str]]) -> dict:
    if not 1 <= len(entries) <= 50:
        raise ValueError("Configura entre 1 y 50 usuarios.")
    users = []
    seen = set()
    for raw_username, password in entries:
        username = normalize_username(raw_username)
        if username in seen:
            raise ValueError(f"El usuario {username!r} está repetido.")
        if not 12 <= len(password) <= 128:
            raise ValueError("Usa contraseñas de entre 12 y 128 caracteres.")
        salt = secrets.token_hex(32)
        users.append({
            "username": username,
            "salt": salt,
            "password_hash": password_digest(password, salt),
            "enabled": True,
        })
        seen.add(username)
    return {"version": 2, "session_secret": secrets.token_hex(32), "users": users}


@dataclass(frozen=True)
class TeamAuth:
    salt: str
    password_hash: str
    session_secret: str

    def verify_password(self, password: str) -> bool:
        return hmac.compare_digest(password_digest(password, self.salt), self.password_hash)

    def verify_credentials(self, username: str | None, password: str) -> str | None:
        return "equipo" if self.verify_password(password) else None

    def verification_username(self) -> str:
        return "equipo"

    def _signature(self, payload: str) -> str:
        # Rotar la contraseña o la clave invalida las sesiones anteriores.
        key = bytes.fromhex(self.session_secret)
        return hmac.new(key, (self.password_hash + ":" + payload).encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self, username: str | None = None, now: int | None = None) -> str:
        now = int(time.time()) if now is None else now
        body = json.dumps({"sub": "equipo", "iat": now, "exp": now + SESSION_SECONDS, "nonce": secrets.token_hex(16)}, separators=(",", ":"))
        payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return payload + "." + self._signature(payload)

    def authenticated_user(self, token: str | None, now: int | None = None) -> str | None:
        if not token or len(token) > 1024 or not token.isascii():
            return None
        try:
            payload, signature = token.split(".")
            if not hmac.compare_digest(signature, self._signature(payload)):
                return None
            body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            now = int(time.time()) if now is None else now
            valid = (body.get("sub") == "equipo" and type(body["iat"]) is int and type(body["exp"]) is int
                     and body["iat"] <= now + 30 and now < body["exp"]
                     and body["exp"] - body["iat"] == SESSION_SECONDS)
            return "equipo" if valid else None
        except (ValueError, KeyError, TypeError, UnicodeError):
            return None

    def valid(self, token: str | None, now: int | None = None) -> bool:
        return self.authenticated_user(token, now) is not None


@dataclass(frozen=True)
class UserCredential:
    username: str
    salt: str
    password_hash: str
    enabled: bool = True


@dataclass(frozen=True)
class UserAuth:
    users: tuple[UserCredential, ...]
    session_secret: str

    def _user(self, username: str) -> UserCredential | None:
        return next((user for user in self.users if user.username == username), None)

    def verify_credentials(self, username: str | None, password: str) -> str | None:
        try:
            normalized = normalize_username(username or "")
        except ValueError:
            normalized = ""
        user = self._user(normalized)
        # Ejecuta PBKDF2 también para usuarios inexistentes para reducir enumeración por tiempo.
        reference = user or self.users[0]
        matches = hmac.compare_digest(password_digest(password, reference.salt), reference.password_hash)
        return user.username if user and user.enabled and matches else None

    def verification_username(self) -> str:
        user = next((item for item in self.users if item.enabled), None)
        if not user:
            raise ValueError("No hay usuarios habilitados.")
        return user.username

    def _signature(self, payload: str, password_hash: str) -> str:
        key = bytes.fromhex(self.session_secret)
        return hmac.new(key, (password_hash + ":" + payload).encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self, username: str, now: int | None = None) -> str:
        user = self._user(normalize_username(username))
        if not user or not user.enabled:
            raise ValueError("Usuario no disponible.")
        now = int(time.time()) if now is None else now
        body = json.dumps({"sub": user.username, "iat": now, "exp": now + SESSION_SECONDS, "nonce": secrets.token_hex(16)}, separators=(",", ":"))
        payload = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return payload + "." + self._signature(payload, user.password_hash)

    def authenticated_user(self, token: str | None, now: int | None = None) -> str | None:
        if not token or len(token) > 1024 or not token.isascii():
            return None
        try:
            payload, signature = token.split(".")
            body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            user = self._user(normalize_username(body["sub"]))
            if not user or not user.enabled or not hmac.compare_digest(signature, self._signature(payload, user.password_hash)):
                return None
            now = int(time.time()) if now is None else now
            valid = (type(body["iat"]) is int and type(body["exp"]) is int
                     and body["iat"] <= now + 30 and now < body["exp"]
                     and body["exp"] - body["iat"] == SESSION_SECONDS)
            return user.username if valid else None
        except (ValueError, KeyError, TypeError, UnicodeError):
            return None

    def valid(self, token: str | None, now: int | None = None) -> bool:
        return self.authenticated_user(token, now) is not None


@lru_cache(maxsize=1)
def auth_config() -> TeamAuth | UserAuth | None:
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
        if data.get("version") == 1:
            for key in ("salt", "password_hash", "session_secret"):
                if not isinstance(data[key], str) or len(data[key]) != 64 or len(bytes.fromhex(data[key])) != 32:
                    raise ValueError("Formato incorrecto")
            return TeamAuth(data["salt"], data["password_hash"], data["session_secret"])
        if data.get("version") == 2:
            secret = data.get("session_secret")
            if not isinstance(secret, str) or len(secret) != 64 or len(bytes.fromhex(secret)) != 32:
                raise ValueError("Formato incorrecto")
            raw_users = data.get("users")
            if not isinstance(raw_users, list) or not 1 <= len(raw_users) <= 50:
                raise ValueError("Usuarios incorrectos")
            users = []
            seen = set()
            for entry in raw_users:
                username = normalize_username(entry["username"])
                if username in seen or type(entry.get("enabled", True)) is not bool:
                    raise ValueError("Usuario repetido o incorrecto")
                for key in ("salt", "password_hash"):
                    value = entry.get(key)
                    if not isinstance(value, str) or len(value) != 64 or len(bytes.fromhex(value)) != 32:
                        raise ValueError("Formato incorrecto")
                users.append(UserCredential(username, entry["salt"], entry["password_hash"], entry.get("enabled", True)))
                seen.add(username)
            return UserAuth(tuple(users), secret)
        raise ValueError("Versión no admitida")
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
