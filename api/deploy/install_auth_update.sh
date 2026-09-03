#!/usr/bin/env bash
# Actualización acotada al servicio pulso-api; no cambia Nginx, UFW ni PostgreSQL.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Ejecuta este instalador con sudo.'; exit 1; }
PULSO_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
PULSO_TARGET=/home/ubuntu/pulso-api
PULSO_UNIT=/etc/systemd/system/pulso-api.service
[[ -f "$PULSO_TARGET/api/dashboard_api.py" && -x "$PULSO_TARGET/.venv/bin/python" && -f "$PULSO_UNIT" ]] || {
  echo 'No se encontró la instalación esperada. No se ha cambiado nada.'; exit 1;
}
[[ ! -L /etc/pulso-api-auth.json && ! -L "$PULSO_UNIT" ]] || {
  echo 'Se encontró un enlace no esperado. Revisar antes de continuar.'; exit 1;
}
cd "$PULSO_SOURCE"
if [[ ! -e /etc/pulso-api-auth.json ]]; then
  "$PULSO_TARGET/.venv/bin/python" -m api.deploy.configure_auth
fi
[[ "$(stat -c '%u:%a' /etc/pulso-api-auth.json)" == '0:600' ]] || {
  echo 'La credencial debe pertenecer a root y tener permisos 600.'; exit 1;
}
DASHBOARD_REQUIRE_AUTH=1 DASHBOARD_AUTH_FILE=/etc/pulso-api-auth.json \
  "$PULSO_TARGET/.venv/bin/python" -c 'from api.auth import auth_config; auth_config(); print("Credencial válida.")'

PULSO_BACKUP=$(mktemp -d /var/backups/pulso-api-auth.XXXXXX)
cp -a "$PULSO_TARGET/api/dashboard_api.py" "$PULSO_BACKUP/dashboard_api.py"
cp -a "$PULSO_UNIT" "$PULSO_BACKUP/pulso-api.service"
if [[ -f "$PULSO_TARGET/api/auth.py" ]]; then cp -a "$PULSO_TARGET/api/auth.py" "$PULSO_BACKUP/auth.py"; fi
echo "Copia de seguridad: $PULSO_BACKUP"

rollback() {
  trap - ERR
  echo 'La actualización falló. Restaurando exclusivamente el servicio de la API.'
  cp -a "$PULSO_BACKUP/dashboard_api.py" "$PULSO_TARGET/api/dashboard_api.py"
  cp -a "$PULSO_BACKUP/pulso-api.service" "$PULSO_UNIT"
  if [[ -f "$PULSO_BACKUP/auth.py" ]]; then cp -a "$PULSO_BACKUP/auth.py" "$PULSO_TARGET/api/auth.py"; fi
  systemctl daemon-reload
  systemctl restart pulso-api.service
  echo 'Se conserva la credencial protegida y la copia de seguridad para revisar el fallo.'
  exit 1
}
trap rollback ERR
install -o ubuntu -g ubuntu -m 644 api/auth.py "$PULSO_TARGET/api/auth.py"
install -o ubuntu -g ubuntu -m 644 api/dashboard_api.py "$PULSO_TARGET/api/dashboard_api.py"
install -o root -g root -m 644 api/deploy/pulso-api.service "$PULSO_UNIT"
systemd-analyze verify "$PULSO_UNIT"
systemctl daemon-reload
systemctl restart pulso-api.service
DASHBOARD_REQUIRE_AUTH=1 DASHBOARD_AUTH_FILE=/etc/pulso-api-auth.json \
  "$PULSO_TARGET/.venv/bin/python" -m api.deploy.check_auth
trap - ERR
echo 'API actualizada y protegida. Nginx, UFW y las bases de datos no se han modificado.'
