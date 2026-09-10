#!/usr/bin/env bash
set -Eeuo pipefail

readonly SOURCE_ROOT="${1:-/home/ubuntu/scripts}"
readonly SITE_CONFIG="/etc/nginx/sites-available/pulso-api"
readonly SNIPPET_SOURCE="$SOURCE_ROOT/production/api/despliegue/nginx-bat-test.conf"
readonly SNIPPET_TARGET="/etc/nginx/snippets/pulso-bat-test.conf"
readonly INCLUDE_LINE="    include /etc/nginx/snippets/pulso-bat-test.conf;"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Ejecuta este instalador con sudo." >&2
    exit 1
fi
[[ -f "$SNIPPET_SOURCE" ]] || { echo "Falta $SNIPPET_SOURCE" >&2; exit 1; }
[[ -f "$SITE_CONFIG" ]] || { echo "No existe $SITE_CONFIG" >&2; exit 1; }
systemctl is-active --quiet tfm-api-test.service || { echo "tfm-api-test.service no está activo." >&2; exit 1; }

service_environment="$(systemctl show tfm-api-test.service --property=Environment --value)"
[[ "$service_environment" == *"TFM_TEST_DB_NAME=tfm_energia_test"* ]] \
    || { echo "El servicio 8011 no apunta a tfm_energia_test." >&2; exit 1; }
[[ "$service_environment" == *"TFM_EMAIL=snapshot-test@local.invalid"* ]] \
    || { echo "El servicio 8011 no usa el usuario aislado esperado." >&2; exit 1; }
curl --silent --show-error --fail http://127.0.0.1:8011/api/bat/ejecuciones \
    | grep -q '"runs"' || { echo "La API aislada no respondió con su catálogo." >&2; exit 1; }

mkdir -p /etc/nginx/snippets /var/backups
backup="$(mktemp /var/backups/pulso-api-nginx-test.XXXXXX)"
cp -a "$SITE_CONFIG" "$backup"
install -m 0644 "$SNIPPET_SOURCE" "$SNIPPET_TARGET"

if ! grep -Fq "$SNIPPET_TARGET" "$SITE_CONFIG"; then
    sed -i "/server_name[[:space:]]\+vps-16d0afbc\.vps\.ovh\.net;/a\\
$INCLUDE_LINE" "$SITE_CONFIG"
fi

if ! nginx -t; then
    cp -a "$backup" "$SITE_CONFIG"
    nginx -t
    echo "La configuración no era válida; se restauró $backup" >&2
    exit 1
fi
systemctl reload nginx

status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    https://vps-16d0afbc.vps.ovh.net/api/bat-test/ejecuciones)"
[[ "$status" == "401" ]] || {
    echo "La ruta quedó publicada, pero sin sesión devolvió HTTP $status en vez de 401." >&2
    exit 1
}

echo "API de estudio aislada publicada y protegida (HTTP 401 sin sesión)."
echo "Base verificada: tfm_energia_test. Copia de seguridad: $backup"
