"""Comprobación local tras instalar: no imprime contraseñas ni tokens."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from api.auth import COOKIE_NAME, auth_config


def main():
    auth = auth_config()
    if auth is None:
        raise SystemExit("La autenticación debe estar activada para esta comprobación.")
    opener = build_opener(ProxyHandler({}))
    for attempt in range(10):
        try:
            with opener.open("http://127.0.0.1:8000/session", timeout=3) as response:
                state = json.load(response)
            if state != {"authenticated": False, "auth_required": True}:
                raise SystemExit("La API no está protegida como se esperaba.")
            break
        except (URLError, TimeoutError):
            if attempt == 9:
                raise SystemExit("La API no respondió al arrancar.")
            time.sleep(1)
    try:
        opener.open("http://127.0.0.1:8000/health", timeout=5).close()
        raise SystemExit("La API entregó datos sin iniciar sesión.")
    except HTTPError as exc:
        if exc.code != 401:
            raise SystemExit(f"Respuesta inesperada sin sesión: HTTP {exc.code}.")
    request = Request("http://127.0.0.1:8000/health", headers={"Cookie": f"{COOKIE_NAME}={auth.issue()}"})
    try:
        with opener.open(request, timeout=25) as response:
            health = json.load(response)
        if health.get("status") != "ok":
            raise ValueError("Respuesta inesperada")
    except (URLError, TimeoutError, ValueError):
        raise SystemExit("El acceso con sesión no pudo verificar PostgreSQL. Revisar el servicio.")
    print("Autenticación y PostgreSQL: OK. Sin sesión, los datos devuelven HTTP 401.")


if __name__ == "__main__":
    main()
