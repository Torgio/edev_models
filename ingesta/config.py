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

def load_cds_key():
    """
    Carga y devuelve el Personal Access Token de Copernicus CDS desde credentials.json
 
    Uso:
        from config import load_cds_key
        cds_key = load_cds_key()
    """
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"\n  credentials.json not found at: {CREDENTIALS_PATH}\n"
            "  Create it with the following structure:\n"
            "  {\n"
            "    \"cds_api_key\": \"YOUR_COPERNICUS_CDS_PERSONAL_ACCESS_TOKEN\"\n"
            "  }"
        )
 
    with open(CREDENTIALS_PATH) as f:
        creds = json.load(f)
 
    if "cds_api_key" not in creds:
        raise KeyError(
            "Falta 'cds_api_key' en credentials.json. "
            "Añade tu Personal Access Token de https://cds.climate.copernicus.eu/profile"
        )

    return creds["cds_api_key"]


def load_anthropic_key():
    """
    Carga y devuelve la clave de API de Anthropic (Claude) desde credentials.json.
    Es una clave PERSONAL con creditos reales -- a diferencia del resto de credenciales de este
    fichero (gratuitas), nunca debe copiarse al credentials.json del servidor sin pensarlo primero.

    Uso:
        from config import load_anthropic_key
        clave = load_anthropic_key()
    """
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"\n  credentials.json not found at: {CREDENTIALS_PATH}\n"
            "  Añade la clave con la estructura:\n"
            "  {\n"
            "    \"anthropic_api_key\": \"sk-ant-...\"\n"
            "  }"
        )

    with open(CREDENTIALS_PATH) as f:
        creds = json.load(f)

    if "anthropic_api_key" not in creds:
        raise KeyError(
            "Falta 'anthropic_api_key' en credentials.json. "
            "Añadela desde https://console.anthropic.com (seccion API Keys)."
        )

    return creds["anthropic_api_key"]