"""Ejecutar con sudo en el servidor; nunca recibe contraseñas por argumentos."""
import getpass
import json
import os
from pathlib import Path

from api.auth import new_credentials


def main():
    path = Path("/etc/pulso-api-auth.json")
    if os.geteuid() != 0:
        raise SystemExit("Ejecutar con sudo para crear la credencial protegida.")
    if path.exists() or path.is_symlink():
        raise SystemExit("La credencial ya existe. No se ha sobrescrito ni cambiado la contraseña.")
    password = getpass.getpass("Nueva contraseña del equipo (16-128 caracteres): ")
    if password != getpass.getpass("Repite la contraseña del equipo: "):
        raise SystemExit("Las contraseñas no coinciden. No se guardó nada.")
    try:
        data = new_credentials(password)
    except ValueError as exc:
        raise SystemExit(str(exc))
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(data, output)
    print("Credencial de equipo creada. Contraseña no mostrada ni guardada en texto plano.")


if __name__ == "__main__":
    main()
