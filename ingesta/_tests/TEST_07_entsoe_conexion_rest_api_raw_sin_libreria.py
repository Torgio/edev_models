import json
from pathlib import Path
import requests


# Endpoint oficial ENTSO-E Transparency Platform
BASE_URL = "https://web-api.tp.entsoe.eu/api"

# credentials.json está dentro de la carpeta ingesta
CREDENTIALS_PATH = Path(__file__).resolve().parent / "credentials.json"


def load_entsoe_token() -> str:
    """
    Lee el token ENTSO-E desde ingesta/credentials.json
    Estructura esperada:
    {
        "entsoe_token": "TU_TOKEN_REAL"
    }
    """
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(f"No encuentro credentials.json en: {CREDENTIALS_PATH}")

    with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
        credentials = json.load(f)

    token = credentials.get("entsoe_token")

    if not token:
        raise ValueError("No existe la clave 'entsoe_token' en credentials.json")

    return token


def test_entsoe_connection():
    token = load_entsoe_token()

    params = {
        "securityToken": token,
        "documentType": "A11",              # Cross-border physical flows
        "processType": "A16",               # Realised
        "in_Domain": "10YES-REE------0",    # España
        "out_Domain": "10YFR-RTE------C",   # Francia
        "periodStart": "202401010000",
        "periodEnd": "202401020000",
    }

    response = requests.get(BASE_URL, params=params, timeout=60)

    safe_url = response.url.replace(token, "TOKEN_OCULTO")

    print("=" * 80)
    print("PRUEBA ENTSO-E")
    print("=" * 80)
    print("Status code:", response.status_code)
    print("URL sin token:")
    print(safe_url)
    print("-" * 80)
    print("Primeros 3000 caracteres de respuesta:")
    print(response.text[:3000])
    print("=" * 80)

    if response.status_code == 200:
        if "Unauthorized" in response.text:
            print("ERROR: token no autorizado o inválido.")
        elif "No matching data found" in response.text:
            print("Conexión OK, pero no hay datos para esa consulta.")
        elif "<Period>" in response.text or "<TimeSeries>" in response.text:
            print("OK: conexión correcta y XML con datos recibido.")
        else:
            print("Respuesta 200 recibida. Revisar XML.")
    elif response.status_code == 401:
        print("ERROR 401: token ausente, inválido o sin permisos REST.")
    elif response.status_code == 400:
        print("ERROR 400: algún parámetro de la consulta es incorrecto.")
    else:
        print(f"Respuesta no esperada: {response.status_code}")


if __name__ == "__main__":
    test_entsoe_connection()