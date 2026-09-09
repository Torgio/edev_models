#!/usr/bin/env bash
# Actualiza SOLO el codigo de la API en el servidor. No toca credenciales, ni
# usuarios SQL, ni Nginx, ni UFW, ni la unidad de systemd.
#
# Por que existe: install_update.sh (1-sep) copiaba dashboard_api.py y
# stored_results.py; desde entonces tambien cambio auth.py. Y
# install_users_update.sh copia los cuatro pero EXIGE rotar la credencial,
# que no es lo que hace falta para publicar un cambio de codigo.
#
# Uso:  sudo ./install_code_update.sh /home/ubuntu/stage-pulso
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo 'Ejecuta este instalador con sudo.'; exit 1; }
[[ $# -eq 1 ]] || { echo 'Uso: install_code_update.sh DIRECTORIO_STAGE'; exit 1; }

SOURCE_DIR="$1"
TARGET_DIR=/home/ubuntu/pulso-api
ACTIVE_AUTH=/etc/pulso-api-auth.json
UNIT=pulso-api.service
RUNTIME_FILES=(api/auth.py api/dashboard_api.py api/peak_accuracy.py api/stored_results.py)

[[ -d "$SOURCE_DIR/api" && ! -L "$SOURCE_DIR" ]] || { echo 'El directorio temporal no es valido.'; exit 1; }
[[ -x "$TARGET_DIR/.venv/bin/python" ]] || { echo 'No se encontro el entorno virtual de pulso-api.'; exit 1; }
[[ -f "$ACTIVE_AUTH" && ! -L "$ACTIVE_AUTH" ]] || { echo 'La credencial activa no existe o es un enlace inesperado.'; exit 1; }
for file in "${RUNTIME_FILES[@]}"; do
  [[ -f "$SOURCE_DIR/$file" ]] || { echo "Falta en el paquete: $file"; exit 1; }
  [[ -f "$TARGET_DIR/$file" && ! -L "$TARGET_DIR/$file" ]] || { echo "Destino inesperado: $TARGET_DIR/$file"; exit 1; }
done

# La credencial que ya esta puesta tiene que seguir siendo valida para el codigo
# NUEVO. auth.py admite version 1 (contrasena de equipo) y version 2 (cuentas
# individuales), pero se comprueba antes de tocar nada, no despues.
echo '1/5 Validando la credencial activa contra el codigo nuevo...'
DASHBOARD_REQUIRE_AUTH=1 DASHBOARD_AUTH_FILE="$ACTIVE_AUTH" PYTHONPATH="$SOURCE_DIR" \
  "$TARGET_DIR/.venv/bin/python" -c \
  'from api.auth import auth_config, TeamAuth, UserAuth
a = auth_config()
assert isinstance(a, (TeamAuth, UserAuth)), a
print("   credencial valida:", "equipo" if isinstance(a, TeamAuth) else f"{len(a.users)} usuarios")'

echo '2/5 Copia de seguridad...'
BACKUP_DIR=$(mktemp -d /var/backups/pulso-api-code.XXXXXX)
mkdir -p "$BACKUP_DIR/api"
for file in "${RUNTIME_FILES[@]}"; do cp -a "$TARGET_DIR/$file" "$BACKUP_DIR/$file"; done
echo "   $BACKUP_DIR"

rollback() {
  trap - ERR
  echo 'La validacion fallo; restaurando la version anterior.' >&2
  for file in "${RUNTIME_FILES[@]}"; do
    install -o ubuntu -g ubuntu -m 644 "$BACKUP_DIR/$file" "$TARGET_DIR/$file"
  done
  systemctl restart "$UNIT"
  echo "Version anterior restaurada. Copia conservada: $BACKUP_DIR" >&2
  exit 1
}
trap rollback ERR

echo '3/5 Instalando y reiniciando...'
for file in "${RUNTIME_FILES[@]}"; do
  install -o ubuntu -g ubuntu -m 644 "$SOURCE_DIR/$file" "$TARGET_DIR/$file"
done
cd "$TARGET_DIR"
"$TARGET_DIR/.venv/bin/python" -m py_compile "${RUNTIME_FILES[@]}"
"$TARGET_DIR/.venv/bin/python" -c \
  'from api.dashboard_api import app
rutas = {r.path for r in app.routes}
faltan = {"/session","/login","/logout","/health","/days","/leaderboard","/peak-accuracy","/performance-history","/performance-options"} - rutas
assert not faltan, faltan
print("   rutas completas:", len(rutas))'
systemctl restart "$UNIT"
systemctl is-active --quiet "$UNIT"

echo '4/5 Comprobando que los datos siguen protegidos...'
session=$(curl --retry 10 --retry-connrefused --retry-delay 1 --max-time 5 \
  --silent --show-error --fail http://127.0.0.1:8000/session)
[[ "$session" == *'"auth_required":true'* && "$session" == *'"authenticated":false'* ]]
for ruta in /health /days /leaderboard /performance-options; do
  code=$(curl --max-time 5 --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:8000$ruta")
  [[ "$code" == "401" ]] || { echo "   $ruta devolvio $code, se esperaba 401" >&2; false; }
done
echo '   sin sesion: 401 en todas las rutas de datos.'

echo '5/5 Listo.'
trap - ERR
echo "API Pulso actualizada y corriendo. Copia de seguridad: $BACKUP_DIR"
systemctl --no-pager --lines=0 status "$UNIT" | head -4
