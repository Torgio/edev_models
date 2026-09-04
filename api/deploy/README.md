# Instalación aislada de la API

Para el estado final del servidor, HTTPS, acceso del equipo, mantenimiento y
recuperación, consultar el [manual de despliegue de Pulso Energía](../../docs/manual_despliegue_pulso_energia.md).
Las instrucciones siguientes describen la preparación técnica del paquete, no
una reinstalación que deba ejecutarse sobre el servidor ya operativo.

Este paquete no contiene credenciales, datos, modelos ni archivos del sitio web.
No modifica los servicios existentes. La API queda limitada a 127.0.0.1:8000;
no abrir el puerto 8000 en UFW ni cambiar el proxy HTTPS hasta validar su acceso.

## Preparación

1. Descomprimir en un directorio nuevo `/home/ubuntu/pulso-api`. Si ya existe,
   detenerse y revisar su contenido: no sobrescribir una instalación anterior.
2. Crear allí un entorno virtual: `python3 -m venv .venv`.
3. Instalar dependencias con `.venv/bin/python -m pip install -r requirements-dashboard.txt`.
4. Configurar un usuario PostgreSQL dedicado que solo pueda consultar las tablas
   `predictions` y `spot_price` de la base correcta. No cambiar usuarios existentes.
5. Completar `api/deploy/pulso-api.env.example` en el servidor, sin enviar los
   valores por chat, y guardarlo como `/etc/pulso-api.env`, propiedad de root y modo 600.
6. Instalar `api/deploy/pulso-api.service` en `/etc/systemd/system/` solo si no existe
   un servicio con ese nombre. Revisar las rutas si el usuario no es `ubuntu`.
7. Preparar la credencial del equipo descrita abajo antes de arrancar: la unidad
   actual exige autenticación y `LoadCredential`.
8. Ejecutar `sudo systemctl daemon-reload` y `sudo systemctl enable --now pulso-api`.
9. Comprobar `/session` y que `/health` devuelva HTTP 401 sin sesión. La salud de
   PostgreSQL debe comprobarse con una sesión válida, no con un curl anónimo.

El servicio carga las credenciales desde variables de entorno. No necesita
`ingesta/credentials.json` ni la clave de ESIOS. Las conexiones tienen un límite
de espera de 10 segundos y las consultas de 15 segundos; se solicita PostgreSQL
en modo de solo lectura. Esto complementa, no sustituye, los permisos SELECT del usuario.

## Acceso del equipo

La API admite cuentas individuales con PBKDF2-SHA256 (600.000 iteraciones y una
sal aleatoria por usuario) y emite una cookie firmada de 8 horas, Secure, HttpOnly
y SameSite=Strict. Todas las cuentas tienen acceso de lectura; no existen roles.
El cierre de sesión borra la cookie de ese navegador; no revoca otras sesiones.
Rotar las credenciales y reiniciar el servicio invalida las sesiones anteriores.

`configure_users.py` solicita cada usuario y contraseña sin mostrarlas y crea un
archivo nuevo con modo 600. Nunca sobrescribe una credencial existente. Systemd
entrega `/etc/pulso-api-auth.json` al servicio mediante `LoadCredential`;
la cuenta `ubuntu` no necesita permiso para leer el original. En modo de servidor,
la falta de la credencial impide arrancar; no se permite un arranque desprotegido.

`install_auth_update.sh` instala únicamente el código de la API y su unidad,
guarda copia de ambos en `/var/backups/pulso-api-auth.*`, reinicia solo la API,
y verifica tanto HTTP 401 sin sesión como PostgreSQL con sesión. Si falla,
restaura la unidad y el código anteriores. No modifica usuarios SQL, Nginx ni UFW.

El límite de login es de 10 intentos por cliente por minuto y 30 globales por
minuto. Está diseñado para un worker; no aumentar workers sin un limitador
compartido. Las peticiones que pasan por Sites comparten límites según la IP
de salida del proxy, por lo que un equipo puede tener que esperar un minuto.

## Condiciones de publicación

La publicación del 31 de agosto de 2026 quedó completada. La web usa
`DASHBOARD_API_URL=https://vps-16d0afbc.vps.ovh.net`. No usar la IP literal desde
el intermediario de Sites; el alojamiento no admite esa llamada directa.

El instalador, por sí solo, no expone la API a Internet. Antes de añadir un proxy en Nginx,
verificar primero la autenticación del equipo. El proxy de Sites valida la sesión
en el backend y rechaza un backend remoto con auth_required=false. CORS no sustituye
la autenticación. No colocar contraseñas ni tokens privados en variables
`NEXT_PUBLIC_*` de la web.

Conservar el sitio HTTP actual y su ruta ACME para la renovación del certificado.
Nginx debe recargarse tras cada renovación correcta; configurar ese deploy-hook
con Certbot y comprobarlo con una renovación de prueba.

Para detener exclusivamente esta API: `sudo systemctl stop pulso-api`.
