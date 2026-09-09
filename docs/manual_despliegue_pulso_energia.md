# Pulso Energía — Manual de instalación y operación

Fecha de cierre del despliegue: **31 de agosto de 2026**. Versión del manual: 1.0.

Actualización preparada posteriormente: [integración del registro común](integracion_registro_oficial.md).
Ese anexo distingue lo validado localmente de lo instalado; este manual conserva la referencia del despliegue v1.

Documento interno para el equipo. No contiene contraseñas, cookies, claves privadas ni tokens. Incluye direcciones y rutas de infraestructura: compartirlo solo con quienes administran el proyecto.

## 1. Qué quedó funcionando

Pulso Energía permite consultar previsiones horarias del precio eléctrico, comparar modelos y visualizar una simulación operativa de batería BESS.

- Web del equipo: <https://pulso-energia-tfm.maguicervinio.chatgpt.site/>.
- API de producción: <https://vps-16d0afbc.vps.ovh.net>.
- Servidor: `91.134.143.153`, usuario de administración `ubuntu`.
- Base de datos: PostgreSQL, `tfm_energia`.
- Acceso: cuentas individuales de usuario, con sesiones de ocho horas.

La web funciona sin que el ordenador de desarrollo esté encendido. Depende del alojamiento de Sites y de que el VPS, Nginx, la API y PostgreSQL estén disponibles.

El acceso correcto al dashboard fue confirmado por la administradora. También se comprobó que las consultas sin sesión no entregan datos.

### Alcance y fuentes de esta documentación

El manual se basa en el código local de `api/` y `app/`, las respuestas de publicación de Sites y las salidas de terminal del servidor compartidas durante la instalación. No constituye una auditoría completa del VPS. Los comandos del servidor fueron ejecutados por la administradora mediante SSH; no se dispone de una inspección final integral de todos los archivos remotos.

## 2. Arquitectura y recorrido de los datos

El navegador consulta la web de Sites. La web reenvía las solicitudes autorizadas a la API por HTTPS. Nginx recibe esas solicitudes en el VPS y las pasa a FastAPI en `127.0.0.1:8000`. FastAPI consulta PostgreSQL mediante un usuario de solo lectura.

Responsabilidades:

- **Navegador:** pantalla de acceso, gráficos, controles y tablas. No recibe credenciales de PostgreSQL.
- **Sites:** aloja la web y su intermediario de API, bajo `/api/dashboard/`. Solo reenvía rutas y parámetros permitidos y la cookie de sesión de Pulso.
- **Nginx:** termina HTTPS y dirige el tráfico a la API local.
- **FastAPI/Uvicorn:** valida sesiones, consulta datos y calcula los agregados y el plan BESS.
- **PostgreSQL:** conserva predicciones y precios reales.
- **Pipelines existentes:** producen y guardan los datos; no forman parte del servicio web instalado.

### De dónde salen los datos

La API lee `public.predictions` y `public.spot_price`. La web solicita `source=production`; el precio real procede de `spot_price.es_esios` y se relaciona por `datetime`. Las fechas del dashboard se interpretan en `Europe/Madrid`.

La web no ejecuta `scripts/guardar_predicciones.py` ni lee directamente ese archivo. Consume los registros que los procesos de generación hayan guardado en PostgreSQL. Este despliegue no instaló ni verificó la programación automática de todos los modelos.

`ml_predicciones` no es la fuente de esta API. No se realizó una migración, sustitución ni borrado de esa tabla como parte del despliegue.

Al recargar la página se vuelven a consultar los datos. No se añadió actualización periódica automática, WebSocket ni un proceso de sincronización en el navegador. La web arranca en la última fecha disponible devuelta por la API.

### Rutas de la API

Sin sesión: `GET /session`, `POST /login` y `POST /logout`. Las dos últimas gestionan la sesión, no datos de negocio.

Con sesión válida:

- `GET /health`: número total de filas y última actualización de `predictions`; no filtra por `source`.
- `GET /days?source=production`: fechas y cobertura disponibles.
- `GET /predictions/AAAA-MM-DD?source=production`: previsiones y precios reales del día.
- `GET /leaderboard`: evaluaciones guardadas en `model_metrics`, sin filtros.
- `GET /performance-options?source=production`: modelos y semillas con métricas diarias disponibles.
- `GET /performance-history?model=gru&seed=44&days=30&source=production`: serie histórica guardada.
- `GET /bess/AAAA-MM-DD`: plan y resultado BESS guardados para el día.

En la web se accede a estas rutas con el prefijo `/api/dashboard`. No se publicaron interfaces Swagger, ReDoc ni el esquema OpenAPI de FastAPI.

## 3. Qué ya existía y qué se añadió

### Infraestructura encontrada

Se informó Ubuntu 24.04.4 LTS, Python 3.12.3, Nginx, PostgreSQL 16, MySQL, MongoDB, InfluxDB, SSH, cron y fail2ban. Los paquetes `python3-venv` y `python3.12-venv` estaban instalados; `snap` también estaba disponible.

Nginx tenía un sitio HTTP por defecto, con raíz `/var/www/html`, que contenía la página de bienvenida. Se conservó para no sustituir el sitio existente y para validar certificados mediante HTTP.

Las comprobaciones puntuales indicaron aproximadamente 3,7 GiB de RAM y 4 GiB de swap. Son datos históricos, no una medición actual ni una garantía de capacidad.

### Componentes añadidos o configurados

- API aislada en `/home/ubuntu/pulso-api`, con su propio entorno virtual `.venv`.
- Dependencias fijadas: FastAPI `0.116.1`, Uvicorn `0.35.0` y psycopg2-binary `2.9.12`.
- Servicio `pulso-api.service`, habilitado para arrancar con el sistema.
- Rol PostgreSQL `pulso_dashboard`, dedicado a consultas.
- Configuración de conexión en `/etc/pulso-api.env`.
- Credencial de acceso del equipo en `/etc/pulso-api-auth.json`.
- Sitio adicional de Nginx `pulso-api`, con HTTPS y proxy a la API.
- Certbot `5.7.0`, instalado mediante snap, y dos certificados de Let's Encrypt.
- Apertura de los puertos TCP 80 y 443 para HTTP/HTTPS durante la preparación.
- Web publicada en Sites con protección de datos por contraseña.

No se reinstalaron ni sustituyeron PostgreSQL, MySQL, MongoDB o InfluxDB. No se modificaron los datos de las tablas para publicar el dashboard. No se abrió el puerto 8000 hacia Internet.

### Advertencia sobre reglas de red anteriores

La salida inicial de UFW mostraba reglas `ALLOW Anywhere` para 5432, 3306, 8086 y 27017, además de reglas para una IP concreta. Esas reglas anteriores no se cambiaron. Permiten tráfico en el firewall del sistema; la accesibilidad efectiva también depende de la escucha de cada servicio y de posibles firewalls externos.

Revisarlas es una tarea de seguridad separada. No cerrarlas sin identificar antes qué aplicaciones y personas dependen de ellas. Nunca cambiar SSH ni reglas de bases de datos como parte de una actualización rutinaria de Pulso.

## 4. Secuencia del despliegue

1. Se revisaron sistema operativo, versiones, servicios, puertos, Nginx, UFW y recursos disponibles.
2. Se preparó HTTPS inicialmente para la IP `91.134.143.153`, con una prueba de emisión y después un certificado real.
3. Se añadió un sitio HTTPS de Nginx que inicialmente devolvía un mensaje de preparación, sin exponer todavía la API.
4. Se configuró la renovación del certificado de la IP y la recarga de Nginx.
5. Se prepararon el directorio aislado, el entorno virtual y las dependencias de la API.
6. Se verificaron `tfm_energia`, `predictions` y `spot_price`; se creó el rol dedicado y se configuró la conexión local.
7. Se instaló y arrancó `pulso-api.service` en `127.0.0.1:8000`. La consulta inicial de salud accedió a PostgreSQL.
8. Se instaló la actualización de autenticación, creando una contraseña de equipo de forma interactiva, sin mostrarla ni guardarla en texto plano.
9. El instalador hizo una copia de seguridad, reinició solo la API y comprobó acceso autenticado a PostgreSQL y rechazo sin sesión.
10. Se activó el proxy HTTPS de Nginx hacia la API protegida.
11. Se publicó la web con pantalla de acceso y se habilitó el enlace público con autorización de la administradora. Los datos siguieron protegidos por la API.
12. Se detectó que el alojamiento de Sites, basado en Cloudflare Workers, no podía consultar directamente la URL con IP. La web devolvía 503 sin entregar datos.
13. Se verificó el nombre existente `vps-16d0afbc.vps.ovh.net`, que resuelve al VPS. No fue necesario comprar un dominio.
14. Se emitió un certificado adicional para ese nombre y se añadió su bloque HTTPS en Nginx, conservando el de la IP.
15. Se cambió `DASHBOARD_API_URL` en Sites al nombre del VPS y se volvió a desplegar la misma versión de la web.
16. Se verificaron las respuestas públicas, la administradora confirmó que podía entrar y se probó la renovación con recarga de Nginx del segundo certificado.

## 5. Archivos y configuración

### En el ordenador de desarrollo

Repositorio: `/Users/magui/git/edev_models`.

- `api/dashboard_api.py`: consultas y rutas FastAPI.
- `api/auth.py`: usuarios, hashes de contraseña, firma de sesiones y límite de intentos.
- `requirements-dashboard.txt`: dependencias Python de la API.
- `api/deploy/pulso-api.service`: unidad de servicio.
- `api/deploy/pulso-api.env.example`: ejemplo sin credenciales reales.
- `api/deploy/configure_auth.py`: creación interactiva de la credencial.
- `api/deploy/configure_users.py`: creación interactiva del archivo de cuentas individuales.
- `api/deploy/check_auth.py`: comprobación local de autenticación y PostgreSQL.
- `api/deploy/install_auth_update.sh`: actualización acotada de código y unidad.
- `app/components/team-access.tsx`: pantalla de acceso y cierre de sesión.
- `app/lib/dashboard-proxy.ts`: intermediario seguro hacia la API.
- `app/app/api/dashboard/[...path]/route.ts`: rutas del intermediario.
- `app/app/page.tsx`: dashboard.
- `app/public/og.png`: portada del enlace compartido, sin datos privados.

La carpeta `app/` tiene su propio repositorio Git. Publicar la web no equivale a guardar o subir todo el repositorio padre, los modelos o las modificaciones de ingesta.

### En el VPS

- `/home/ubuntu/pulso-api/`: instalación de la API.
- `/home/ubuntu/pulso-api/.venv/`: entorno Python aislado.
- `/etc/systemd/system/pulso-api.service`: servicio activo.
- `/etc/pulso-api.env`: variables de PostgreSQL; propietario root, permisos 600.
- `/etc/pulso-api-auth.json`: usuarios, hashes de contraseña y secreto de firma; propietario root, permisos 600.
- `/etc/nginx/sites-available/pulso-api`: configuración HTTPS añadida.
- `/etc/nginx/sites-enabled/pulso-api`: enlace que habilita el sitio.
- `/var/www/html`: raíz HTTP conservada para las validaciones ACME.
- `/etc/letsencrypt/`: certificados, claves y configuración de renovación.
- `/var/log/letsencrypt/letsencrypt.log`: registro de Certbot.

El instalador de autenticación copia los archivos de ejecución y la unidad, no todo el directorio `api/deploy`. Los auxiliares pueden estar únicamente en el paquete extraído; comprobar su ubicación antes de ejecutarlos.

### Variables de la API

La unidad fija `DASHBOARD_DB_MODE=environment` y `DASHBOARD_REQUIRE_AUTH=1`.

El archivo protegido de entorno configura:

```ini
PGHOST=127.0.0.1
PGPORT=5432
PGDATABASE=tfm_energia
PGUSER=pulso_dashboard
PGPASSWORD=<secreto configurado solo en el servidor>
DASHBOARD_ALLOWED_ORIGINS=https://pulso-energia-tfm.maguicervinio.chatgpt.site
```

Este bloque es explicativo: no sobrescribir el archivo real con el marcador de contraseña. No imprimir el contenido de `/etc/pulso-api.env` en tickets, chats o logs.

### Variables de Sites

Valor de producción final:

```ini
DASHBOARD_API_URL=https://vps-16d0afbc.vps.ovh.net
```

Es una variable del servidor web, no un secreto. Sites no guarda las contraseñas de acceso ni la contraseña de PostgreSQL. No introducirlas en `NEXT_PUBLIC_*` ni en código del navegador.

Al cierre, el comentario de `app/.env.example` y el valor de respaldo del código aún mencionan la IP inicial. No usarlos como referencia de producción: prevalece la variable de Sites indicada arriba. La IP directa no sirve para este intermediario alojado.

## 6. Seguridad y acceso

### Tres credenciales distintas

- **Contraseña de Ubuntu:** administración por SSH/sudo.
- **Contraseña de `pulso_dashboard`:** conexión de la API a PostgreSQL.
- **Contraseña individual:** acceso de cada persona al dashboard.

No son intercambiables ni deben reutilizarse entre sí.

### Usuarios, contraseñas y sesiones

Cada contraseña tiene entre 12 y 128 caracteres. Se guarda un derivado PBKDF2-HMAC-SHA256 de 600.000 iteraciones con una sal distinta por usuario, nunca la contraseña en texto plano. El archivo también contiene un secreto de firma y sigue siendo material sensible.

La cookie `pulso_session` está firmada con HMAC-SHA256, tiene una duración fija de ocho horas y utiliza `Secure`, `HttpOnly` y `SameSite=Strict`. No se guarda la contraseña en `localStorage`.

La API debe fallar al arrancar si falta su credencial de producción. Systemd la entrega mediante `LoadCredential=team-auth:/etc/pulso-api-auth.json`.

La web comprueba la sesión en la API y rechaza un backend remoto que indique `auth_required=false`. El navegador no puede habilitar el acceso a los datos simplemente ocultando la pantalla de login.

El enlace es público, pero las consultas a los datos requieren sesión. CORS no sustituye esta autorización.

### Límites y alcance

- Hay cuentas individuales, pero todas tienen el mismo permiso funcional de lectura; no hay roles ni MFA.
- Deshabilitar un usuario o cambiar su contraseña invalida sus sesiones cuando se instala el nuevo archivo de credenciales y se reinicia la API.
- Cerrar sesión elimina la cookie de ese navegador; no revoca otras sesiones válidas del mismo usuario.
- El límite de login es de 10 intentos por cliente y 30 globales por minuto. Los visitantes pueden compartir la IP de salida del intermediario.
- El limitador está en memoria y diseñado para un solo worker. No aumentar workers sin revisar este mecanismo.
- La excepción sin contraseña del desarrollo local no debe activarse en producción.

Para crear las cuentas se ejecuta `api.deploy.configure_users` de forma interactiva. El auxiliar no acepta contraseñas por argumentos y no sobrescribe archivos existentes. Para añadir, deshabilitar o cambiar una cuenta se genera primero un archivo nuevo, se respalda la credencial activa, se valida la nueva configuración y se reinicia exclusivamente la API. No borrar la credencial activa a ciegas.

### Permisos de base de datos y servicio

El rol dedicado se creó con LOGIN, sin superusuario, creación de bases, creación de roles ni replicación, con CONNECT a `tfm_energia`, USAGE del esquema `public` y SELECT de las dos tablas utilizadas. Se configuró lectura por defecto.

Cada conexión solicita además modo de solo lectura, 10 segundos de espera de conexión y un máximo de 15 segundos por consulta. Estas opciones complementan los permisos SQL; no los sustituyen.

El servicio corre como `ubuntu`, con un worker, límite de concurrencia 16 y reinicio ante fallos. Incluye `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`, `ProtectHome=read-only` y `UMask=0077`. No es una separación mediante contenedor ni una cuenta de sistema exclusiva.

## 7. HTTPS, certificados y Nginx

Se mantienen dos certificados independientes:

- IP: `/etc/letsencrypt/live/91.134.143.153/`. Fue emitido con el perfil `shortlived`. La primera emisión caducaba el 6 de septiembre de 2026.
- Nombre del VPS: `/etc/letsencrypt/live/vps-16d0afbc.vps.ovh.net/`. La primera emisión caducaba el 29 de noviembre de 2026. Es el utilizado por Sites.

En cada directorio, `fullchain.pem` contiene el certificado y `privkey.pem` la clave privada. Nunca copiar las claves a la web, Git o un documento. Las fechas anteriores son históricas: tras renovar cambian.

Los certificados se emitieron con `certonly --webroot --webroot-path /var/www/html`; Certbot no instaló automáticamente los bloques Nginx. Se hicieron pruebas `--dry-run` antes de emitirlos.

Se añadió el siguiente bloque para el nombre del VPS. Es una referencia documental, no un archivo completo para sobrescribir Nginx:

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name vps-16d0afbc.vps.ovh.net;

    ssl_certificate /etc/letsencrypt/live/vps-16d0afbc.vps.ovh.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vps-16d0afbc.vps.ovh.net/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 25s;
        client_max_body_size 2k;
    }
}
```

El bloque de la IP conserva su propio `server_name` y certificado. El sitio HTTP por defecto se mantuvo; no se documenta una redirección general de HTTP a HTTPS añadida por este trabajo.

Certbot informó que dejó programada la renovación. En ambos certificados se guardó y probó el siguiente deploy-hook:

```bash
/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx
```

Así Nginx carga el certificado renovado solo si su configuración es válida. El mensaje de Certbot `Hook 'deploy-hook' ran with error output` acompañado por `syntax is ok`, `test is successful` y `Successfully updated configuration` se debió a que Nginx escribe esos mensajes en stderr; en las pruebas realizadas no indicó un fallo.

## 8. Uso y límites funcionales del dashboard

1. Abrir la URL de la web e introducir el usuario y la contraseña individual.
2. Elegir el día, avanzar o retroceder y seleccionar los modelos visibles.
3. Consultar curvas horarias, precios reales cuando estén disponibles y tabla de resultados.
4. Revisar el ranking MAE y el plan BESS por modelo y duración.
5. Recargar para consultar nuevos registros; cerrar sesión al terminar en equipos compartidos.

La interfaz contempla 13 identificadores de modelos; que aparezcan en el código no significa que todos tengan predicciones para todas las fechas. La disponibilidad efectiva depende de PostgreSQL. Incorporar un nuevo modelo puede requerir actualizar la lista de la interfaz, además de guardar sus resultados.

El ranking de 30 días se calcula respecto a la fecha máxima disponible en las predicciones del origen seleccionado, no necesariamente respecto a hoy. Las observaciones con precio real pueden diferir por modelo; comparar también la cobertura.

El cálculo BESS es una **simulación simplificada**, no una orden de operación ni una rentabilidad neta garantizada:

- Potencia nominal de 1 MW y duración seleccionable de 1, 2 o 4 horas.
- Eficiencia del 90 %, modelada como menor energía entregada en descarga.
- Un ciclo diario, con todas las cargas anteriores a todas las descargas.
- Ingreso esperado según previsión; realizado según precio real; oráculo como comparación con conocimiento del precio real.
- Sin degradación, costes operativos, peajes ni restricciones completas de una batería real.
- El algoritmo fuerza un ciclo, incluso si el mejor resultado es negativo.
- No se acreditó validación exhaustiva para horas faltantes, cobertura desigual entre modelos o cambios de horario de verano/invierno.

La interfaz conserva textos e indicadores ilustrativos y una curva de demostración como respaldo ante ciertos fallos. **No todos los highlights son métricas operativas verificadas.** Si aparece estado de error o demostración, no interpretar esas cifras como registros nuevos de la base de datos. La etiqueta «Pipeline al día» no constituye por sí sola una comprobación de salud del pipeline.

## 9. Comprobaciones rutinarias

Salvo indicación contraria, ejecutar en el VPS, dentro de la sesión SSH. Estos comandos consultan estado; no cambian datos.

### Servicio y registros

```bash
sudo systemctl is-active pulso-api.service
sudo systemctl is-enabled pulso-api.service
sudo systemctl status pulso-api.service --no-pager
sudo journalctl -u pulso-api.service -n 100 --no-pager
sudo nginx -t
sudo ss -ltnp
```

Resultado esperado: API activa y habilitada, Nginx válido y API escuchando en `127.0.0.1:8000`, no en `0.0.0.0:8000` ni `[::]:8000`. Los registros pueden incluir datos internos; revisar antes de compartirlos.

### Acceso anónimo directo a la API

```bash
curl --silent --show-error \
  https://vps-16d0afbc.vps.ovh.net/session

curl --silent --show-error -o /dev/null \
  -w '\nHTTP %{http_code}\n' \
  https://vps-16d0afbc.vps.ovh.net/health
```

Se espera `{"authenticated":false,"auth_required":true}` y `HTTP 401`. Un 401 sin sesión es correcto, pero no demuestra por sí solo que PostgreSQL esté sano: la consulta se rechaza antes de acceder a la base.

### Acceso anónimo a través de la web

```bash
curl --silent --show-error \
  https://pulso-energia-tfm.maguicervinio.chatgpt.site/api/dashboard/session

curl --silent --show-error -o /dev/null \
  -w '\nHTTP %{http_code}\n' \
  https://pulso-energia-tfm.maguicervinio.chatgpt.site/api/dashboard/health
```

Se esperan los mismos resultados. Para verificar datos autenticados, entrar normalmente en la web y comprobar la fecha, el estado de datos y los modelos; no pegar la contraseña o la cookie en comandos ni chats.

### Certificados y renovación

```bash
sudo /snap/bin/certbot certificates
sudo systemctl list-timers --all --no-pager
```

Comprobar las fechas actuales y el mecanismo programado de Certbot. Para una prueba administrativa de renovación del nombre del VPS:

```bash
sudo /snap/bin/certbot renew \
  --cert-name vps-16d0afbc.vps.ovh.net \
  --dry-run \
  --run-deploy-hooks
```

Esta última orden es una simulación de renovación, pero **sí ejecuta el hook y puede recargar Nginx**. Requiere que DNS y HTTP/ACME sigan funcionando. No ejecutar repetidamente sin motivo.

### Permisos de secretos, sin mostrar su contenido

```bash
sudo stat -c '%U %a %n' /etc/pulso-api.env /etc/pulso-api-auth.json
```

Se espera propietario `root` y modo `600` para ambos.

## 10. Actualizaciones y recuperación

### API

Antes de actualizar, revisar cambios y dependencias, conservar una copia protegida de la versión actual y disponer de una ventana de recuperación. No reemplazar carpetas de bases de datos ni credenciales para actualizar código.

El instalador de autenticación utilizado fue:

```text
/home/ubuntu/pulso-auth-update-40iduT/api/deploy/install_auth_update.sh
```

Se ejecutó con sudo desde el paquete extraído. Instala exclusivamente `api/auth.py`, `api/dashboard_api.py` y la unidad; no actualiza dependencias ni publica la web. Su validación comprueba acceso local sin sesión y con sesión a PostgreSQL. No es un instalador general para cualquier futura versión del proyecto.

Después de un cambio revisado de código o variables, el reinicio acotado es:

```bash
sudo systemctl restart pulso-api.service
```

Si se modificó la unidad systemd, ejecutar antes `sudo systemctl daemon-reload`. Habrá una breve interrupción de la API. No reiniciar PostgreSQL, MySQL, MongoDB o el servidor completo para una actualización normal de Pulso.

### Web

Mantener el proyecto `app/`, comprobar la compilación, guardar la fuente exacta en su repositorio de Sites y publicar la versión validada. La web pública requiere conservar la protección de datos en servidor.

La publicación de cierre utilizó la versión 3, commit `ddc3871aa460982064b8ebfeb564eedebdcdb8b5`. La corrección del dominio reutilizó esa versión y cambió únicamente la variable de entorno de Sites. Identificador del proyecto: `appgprj_6a944ae54ca881918834f3706ee0f9cf`.

Cambiar archivos locales no actualiza producción. Modificar el código Python no despliega la web, y publicar la web no copia Python al VPS. Para cambios de entorno de Sites, volver a desplegar y verificar el valor aplicado.

No volver a una versión anterior a la protección por contraseña manteniendo el enlace público. Una reversión debe conservar un par compatible de web y API protegidas.

### Copias existentes y límites

El instalador confirmó una copia en `/var/backups/pulso-api-auth.kFhwKi`. Contiene código/unidad anteriores, **no una copia completa de la base de datos, del servidor ni necesariamente de los secretos**.

Esa copia corresponde a la instalación inicial de autenticación: puede contener una versión sin protección. No restaurarla mientras Nginx siga exponiendo la API. El mecanismo automático de reversión del instalador también debe revisarse antes de reutilizarlo ahora que hay exposición HTTPS.

Se indicaron además copias Nginx `pulso-api.backup-auth` y `pulso-api.backup-dominio`, en `/etc/nginx/sites-available/`. Antes de usarlas, verificar que existen y revisar su contenido y fecha; no habilitarlas como sitios adicionales.

No se configuró ni se comprobó una política de copias completas y restauración de PostgreSQL o del VPS. Una carpeta de respaldo en el mismo servidor no protege frente a pérdida del servidor. Planificar copias externas cifradas y pruebas de restauración como tarea separada.

### Recuperación segura

1. Identificar si falla la web, Nginx, la API o PostgreSQL mediante las comprobaciones anteriores.
2. Si hay riesgo de exposición, detener solo la API con `sudo systemctl stop pulso-api.service`. Es una medida de contención: interrumpe el dashboard, no borra datos.
3. Revisar el respaldo elegido y confirmar que conserva autenticación antes de restaurar cualquier archivo.
4. Validar Nginx con `sudo nginx -t` antes de recargarlo.
5. Arrancar/reiniciar la API protegida y repetir pruebas sin sesión y con sesión.
6. Confirmar el acceso a datos reales desde la web antes de comunicar la recuperación.

No usar borrados recursivos, reinstalaciones generales ni restauraciones de base de datos para resolver errores de contraseña o del proxy.

## 11. Incidencias habituales

### La web muestra servicio no disponible o HTTP 503

Comprobar `/session` directamente en el nombre del VPS y después a través de `/api/dashboard/session` en Sites. Si funciona solo directamente, revisar `DASHBOARD_API_URL`, la versión desplegada y los registros del alojamiento. El valor debe usar el hostname HTTPS, no la IP inicial ni `localhost`.

### HTTP 401

Sin sesión es el comportamiento correcto. Tras ocho horas es necesario volver a entrar. Si ocurre después de introducir las credenciales, comprobar el usuario y la contraseña individual y que el navegador admite la cookie; no desactivar autenticación para diagnosticarlo.

### HTTP 429 al entrar

Se alcanzó el límite de intentos. Esperar un minuto y evitar intentos simultáneos. Las personas que pasan por el intermediario pueden compartir el límite por IP.

### Nginx devuelve HTTP 502 o la API no arranca

Consultar estado y registros de `pulso-api.service`, comprobar las credenciales por su existencia/permisos sin mostrarlas y validar conexión PostgreSQL. Un archivo de usuarios ausente o inválido bloquea deliberadamente el arranque.

### No aparecen datos nuevos

Recargar la web, revisar la fecha seleccionada y confirmar que el pipeline escribió en `tfm_energia.public.predictions` con `source=production`. Verificar el modelo y los timestamps. Que la API esté activa no implica que los pipelines hayan generado nuevas predicciones.

### Falta un modelo o el plan BESS

Comprobar disponibilidad para esa fecha y cobertura horaria completa del modelo. Un modelo conocido por la interfaz puede no tener registros. El plan BESS actual presupone precios suficientes y una serie del modelo en las horas procesadas.

### Error de certificado

Comprobar DNS, certificado vigente, renovación programada, acceso HTTP a la ruta ACME y recarga de Nginx. No desactivar la validación TLS ni sustituir HTTPS por HTTP como solución.

## 12. Cierre y próximos controles

Verificado durante el despliegue:

- Servicio API activo y dependencias importables.
- Acceso de la API a PostgreSQL durante su prueba autenticada.
- HTTPS operativo para IP y nombre del VPS.
- Autenticación obligatoria y rechazo sin sesión tanto directo como a través de Sites.
- 13 pruebas Python y 11 pruebas del intermediario superadas en desarrollo.
- Compilación y publicación de la web completadas.
- Acceso al dashboard confirmado por la administradora.
- Pruebas de renovación y hooks de Nginx completadas para ambos certificados.

Pendiente como trabajo separado, no presentado como instalado:

- Monitorización continua y alertas de caídas, datos atrasados y caducidad TLS.
- Copias externas y prueba de restauración de base de datos/configuración.
- Revisión coordinada de puertos de bases de datos abiertos previamente.
- Inventario y verificación de cron/pipelines de todos los modelos.
- Sustitución de indicadores ilustrativos y respaldos demo en la interfaz.
- Validación funcional más amplia de BESS, huecos horarios y cambios de hora.
- Procedimiento probado de rotación de contraseña y eventual acceso por persona.

No se realizó una prueba de carga ni una auditoría de seguridad exhaustiva. Una publicación correcta y una prueba de acceso no sustituyen esos controles.

### Registro para futuras intervenciones

Anotar en cada cambio: fecha, responsable, motivo, archivos/servicios afectados, versión de web/API, copia previa, pruebas realizadas, resultado y procedimiento de recuperación. Nunca registrar secretos.

Responsable operativo: por completar por el equipo. Ubicación de copias externas y canal de incidencias: por definir.
