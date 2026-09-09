#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo 'Ejecuta este instalador con sudo.'; exit 1; }
[[ $# -eq 2 ]] || { echo 'Uso: install_users_update.sh DIRECTORIO_STAGE CREDENCIAL_NUEVA'; exit 1; }

SOURCE_DIR="$1"
NEW_AUTH_FILE="$2"
TARGET_DIR=/home/ubuntu/pulso-api
ACTIVE_AUTH=/etc/pulso-api-auth.json
RUNTIME_FILES=(api/auth.py api/dashboard_api.py api/peak_accuracy.py api/stored_results.py)

[[ -d "$SOURCE_DIR/api" && ! -L "$SOURCE_DIR" ]] || {
  echo 'El directorio temporal no es válido.'; exit 1;
}
[[ -f "$NEW_AUTH_FILE" && ! -L "$NEW_AUTH_FILE" ]] || {
  echo 'La credencial nueva no existe o no es un archivo regular.'; exit 1;
}
[[ -f "$ACTIVE_AUTH" && ! -L "$ACTIVE_AUTH" ]] || {
  echo 'La credencial activa no existe o es un enlace no esperado.'; exit 1;
}
[[ -x "$TARGET_DIR/.venv/bin/python" ]] || {
  echo 'No se encontró el entorno virtual de pulso-api.'; exit 1;
}
for file in "${RUNTIME_FILES[@]}"; do
  [[ -f "$SOURCE_DIR/$file" && -f "$TARGET_DIR/$file" ]] || {
    echo "Falta el archivo requerido: $file"; exit 1;
  }
done

DASHBOARD_REQUIRE_AUTH=1 \
DASHBOARD_AUTH_FILE="$NEW_AUTH_FILE" \
PYTHONPATH="$SOURCE_DIR" \
  "$TARGET_DIR/.venv/bin/python" -c \
  'from api.auth import auth_config, UserAuth; a=auth_config(); assert isinstance(a, UserAuth); print("Nueva credencial válida:", len(a.users), "usuarios")'

BACKUP_DIR=$(mktemp -d /var/backups/pulso-api-users.XXXXXX)
mkdir -p "$BACKUP_DIR/api"
cp -a "$ACTIVE_AUTH" "$BACKUP_DIR/pulso-api-auth.json"
for file in "${RUNTIME_FILES[@]}"; do
  cp -a "$TARGET_DIR/$file" "$BACKUP_DIR/$file"
done
echo "Copia de seguridad: $BACKUP_DIR"

rollback() {
  trap - ERR
  echo 'La validación falló; restaurando la versión anterior.'
  install -o root -g root -m 600 "$BACKUP_DIR/pulso-api-auth.json" "$ACTIVE_AUTH"
  for file in "${RUNTIME_FILES[@]}"; do
    install -o ubuntu -g ubuntu -m 644 "$BACKUP_DIR/$file" "$TARGET_DIR/$file"
  done
  systemctl restart pulso-api.service
  echo "Versión anterior restaurada. Copia conservada: $BACKUP_DIR"
  exit 1
}
trap rollback ERR

for file in "${RUNTIME_FILES[@]}"; do
  install -o ubuntu -g ubuntu -m 644 "$SOURCE_DIR/$file" "$TARGET_DIR/$file"
done
install -o root -g root -m 600 "$NEW_AUTH_FILE" "$ACTIVE_AUTH"
systemctl restart pulso-api.service

cd "$TARGET_DIR"
DASHBOARD_REQUIRE_AUTH=1 \
DASHBOARD_AUTH_FILE="$ACTIVE_AUTH" \
PYTHONPATH="$TARGET_DIR:$SOURCE_DIR" \
  "$TARGET_DIR/.venv/bin/python" "$SOURCE_DIR/api/deploy/check_auth.py"

trap - ERR
rm -f "$NEW_AUTH_FILE"
echo 'Usuarios instalados correctamente.'
echo "Copia de seguridad: $BACKUP_DIR"
