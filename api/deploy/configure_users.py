"""Crea de forma interactiva un archivo protegido de usuarios del dashboard."""
import argparse
import getpass
import json
import os
from pathlib import Path

from api.auth import new_user_credentials, normalize_username


def main():
    parser = argparse.ArgumentParser(description="Configurar usuarios individuales de Pulso Energía.")
    parser.add_argument(
        "--output",
        default="pulso-api-auth.json",
        help="Archivo nuevo que se creará; nunca sobrescribe uno existente.",
    )
    args = parser.parse_args()
    path = Path(args.output).expanduser().resolve()
    if path.exists() or path.is_symlink():
        raise SystemExit(f"El archivo ya existe: {path}. No se ha sobrescrito.")

    try:
        count = int(input("Número de usuarios (1-50) [5]: ").strip() or "5")
    except ValueError:
        raise SystemExit("El número de usuarios no es válido.")
    if not 1 <= count <= 50:
        raise SystemExit("Configura entre 1 y 50 usuarios.")

    entries = []
    for index in range(1, count + 1):
        while True:
            raw_username = input(f"Usuario {index}: ").strip()
            try:
                username = normalize_username(raw_username)
                if any(existing == username for existing, _ in entries):
                    raise ValueError("Ese usuario ya fue agregado.")
                break
            except ValueError as exc:
                print(exc)
        password = getpass.getpass(f"Contraseña de {username} (12-128 caracteres): ")
        repeated = getpass.getpass("Repite la contraseña: ")
        if password != repeated:
            raise SystemExit("Las contraseñas no coinciden. No se guardó ningún archivo.")
        entries.append((username, password))

    try:
        data = new_user_credentials(entries)
    except ValueError as exc:
        raise SystemExit(str(exc))

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(data, output, separators=(",", ":"))
        output.write("\n")
    print(f"Se crearon {count} cuentas en {path}.")
    print("Las contraseñas no se muestran ni se guardan en texto plano.")


if __name__ == "__main__":
    main()
