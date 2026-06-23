"""
TFM Energia UCM — Shared configuration loader
Lee todas las credenciales desde credentials.json
Este fichero SI se sube a GitHub — no contiene datos sensibles.

credentials.json esta en .gitignore y NUNCA se sube a GitHub.
Cada miembro del equipo tiene su propio credentials.json local y en el servidor.
"""

import json
from pathlib import Path

# Busca credentials.json en la misma carpeta que el script que lo importa
CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"


def load_config():
    """
    Carga y devuelve (headers_esios, db_config) desde credentials.json
    
    Uso:
        from config import load_config
        headers, db_config = load_config()
    """
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"\n  credentials.json not found at: {CREDENTIALS_PATH}\n"
            "  Create it with the following structure:\n"
            "  {\n"
            "    \"Host\": \"api.esios.ree.es\",\n"
            "    \"x-api-key\": \"YOUR_TOKEN\",\n"
            "    \"db_host\": \"YOUR_SERVER_IP\",\n"
            "    \"db_port\": 5432,\n"
            "    \"db_name\": \"tfm_energia\",\n"
            "    \"db_user\": \"postgres\",\n"
            "    \"db_password\": \"YOUR_PASSWORD\"\n"
            "  }"
        )

    with open(CREDENTIALS_PATH) as f:
        creds = json.load(f)

    headers = {
        "Host":         creds["Host"],
        "x-api-key":    creds["x-api-key"],
        "Accept":       "application/json; application/vnd.esios-api-v2+json",
        "Content-Type": "application/json",
    }

    db_config = {
        "host":     creds["db_host"],
        "port":     int(creds["db_port"]),
        "dbname":   creds["db_name"],
        "user":     creds["db_user"],
        "password": creds["db_password"],
    }

    return headers, db_config
