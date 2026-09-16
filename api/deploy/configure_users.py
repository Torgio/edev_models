"""Crea de forma interactiva un archivo protegido de usuarios del dashboard."""
import argparse
import getpass
import json
import os
from pathlib import Path

from api.auth import password_digest, new_user_credentials, normalize_username


def _load_existing_users(path: Path):
    data = json.loads(path.read_text())
    if data.get("version") != 2:
        raise SystemExit("Solo se pueden ampliar credenciales de usuarios individuales (version 2).")
    secret = data.get("session_secret")
    users = data.get("users")
    if not isinstance(secret, str) or not isinstance(users, list):
        raise SystemExit("El archivo existente no tiene el formato esperado.")
    seen = set()
    clean_users = []
    for entry in users:
        if not isinstance(entry, dict):
            raise SystemExit("El archivo existente contiene un usuario no valido.")
        username = normalize_username(str(entry.get("username", "")))
        if username in seen:
            raise SystemExit(f"Usuario repetido en el archivo existente: {username}.")
        salt = entry.get("salt")
        password_hash = entry.get("password_hash")
        enabled = entry.get("enabled", True)
        if not isinstance(salt, str) or not isinstance(password_hash, str) or type(enabled) is not bool:
            raise SystemExit(f"Formato incompleto para el usuario {username}.")
        clean_users.append({"username": username, "salt": salt, "password_hash": password_hash, "enabled": enabled})
        seen.add(username)
    return {"version": 2, "session_secret": secret, "users": clean_users}, seen


def main():
    parser = argparse.ArgumentParser(description="Configurar usuarios individuales de Pulso Energía.")
    parser.add_argument(
        "--output",
        default="pulso-api-auth.json",
        help="Archivo nuevo que se creará; nunca sobrescribe uno existente.",
    )
    parser.add_argument(
        "--append-existing",
        help="Archivo de credenciales existente; conserva session_secret y usuarios, y añade cuentas nuevas en --output.",
    )
    args = parser.parse_args()
    path = Path(args.output).expanduser().resolve()
    if path.exists() or path.is_symlink():
        raise SystemExit(f"El archivo ya existe: {path}. No se ha sobrescrito.")
    existing_data = None
    existing_names: set[str] = set()
    if args.append_existing:
        existing_path = Path(args.append_existing).expanduser().resolve()
        existing_data, existing_names = _load_existing_users(existing_path)

    try:
        default_count = "1" if existing_data else "5"
        count = int(input(f"Número de usuarios nuevos (1-50) [{default_count}]: ").strip() or default_count)
    except ValueError:
        raise SystemExit("El número de usuarios no es válido.")
    if not 1 <= count <= 50 or len(existing_names) + count > 50:
        raise SystemExit("Configura entre 1 y 50 usuarios en total.")

    entries = []
    for index in range(1, count + 1):
        while True:
            raw_username = input(f"Usuario {index}: ").strip()
            try:
                username = normalize_username(raw_username)
                if username in existing_names or any(existing == username for existing, _ in entries):
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
        if existing_data:
            users = list(existing_data["users"])
            for username, password in entries:
                if not 12 <= len(password) <= 128:
                    raise ValueError("Usa contraseñas de entre 12 y 128 caracteres.")
                salt = os.urandom(32).hex()
                users.append({
                    "username": username,
                    "salt": salt,
                    "password_hash": password_digest(password, salt),
                    "enabled": True,
                })
            data = {**existing_data, "users": users}
        else:
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
    print(f"Se crearon {count} cuentas nuevas en {path}.")
    print("Las contraseñas no se muestran ni se guardan en texto plano.")


if __name__ == "__main__":
    main()
