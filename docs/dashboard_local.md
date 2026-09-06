# Pulso Energía: probar los avances en tu ordenador

Esta guía arranca la web y la API **en local**. No ejecutar los instaladores de
`api/deploy`, no publicar en Sites y no modificar Nginx ni servicios del VPS.

## 1. Qué necesitas

- Acceso al repositorio `Torgio/edev_models` y a la rama que comparta el equipo.
- Python 3.12 (la API requiere Python 3.10 o superior).
- Node.js 24 y pnpm 11.19.0. Comprobar con `node --version` y `pnpm --version`.
- Acceso autorizado a PostgreSQL, con un usuario que pueda leer `predictions` y
  `spot_price`, o una copia local autorizada con esas tablas y sus datos.

No hace falta instalar los entrenamientos, notebooks ni todas las dependencias
del repositorio. Tampoco hace falta la contraseña SSH del servidor para ejecutar
la app. Las credenciales PostgreSQL son distintas de la contraseña de la web.
No se distribuyen por Git ni se incluyen en esta guía.

Clonar el repositorio o actualizar tu copia y cambiar a la rama acordada con el
equipo. No se presupone que estos avances estén ya en la rama principal. Desde
la raíz deben existir `api/dashboard_api.py` y `app/package.json`.

## 2. Primera instalación: API

En macOS/Linux, desde la raíz de `edev_models`:

```bash
python3 -m venv .venv-dashboard
.venv-dashboard/bin/python -m pip install -r requirements-dashboard.txt
cp -n .env.dashboard.example .env.dashboard.local
```

En Windows (PowerShell):

```powershell
py -3.12 -m venv .venv-dashboard
.\.venv-dashboard\Scripts\python.exe -m pip install -r requirements-dashboard.txt
if (!(Test-Path .env.dashboard.local)) {
  Copy-Item .env.dashboard.example .env.dashboard.local
}
```

Editar `.env.dashboard.local` en tu ordenador y completar `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER` y `PGPASSWORD` con el acceso autorizado. Si un valor contiene
espacios o `#`, usar comillas según el formato dotenv. El archivo está excluido
de Git. No compartirlo, pegarlo en mensajes ni incorporarlo a capturas.

La API no crea tablas ni carga datos. Solicita transacciones de solo lectura;
conviene que el usuario PostgreSQL también tenga permisos exclusivamente de lectura.
Si usas una base remota, debe ser accesible desde tu red. Si no lo es, consultar
con el responsable; no abrir puertos ni reutilizar usuarios administradores.

## 3. Primera instalación: web

Desde `edev_models/app`:

```bash
pnpm install --frozen-lockfile
```

Copiar `.env.example` a `.env.local` si no existe. En macOS/Linux:

```bash
cp -n .env.example .env.local
```

En PowerShell:

```powershell
if (!(Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

El valor debe ser `DASHBOARD_API_URL=http://127.0.0.1:8000`.
No poner credenciales PostgreSQL en la web ni variables `NEXT_PUBLIC_*`.
Conservar `pnpm-lock.yaml`; no sustituirlo por otro lockfile ni actualizar
dependencias para esta prueba. La configuración `.openai/hosting.json` forma
parte de la compilación: conservarla, pero no ejecutar ninguna publicación.

## 4. Arrancar: dos terminales

**Terminal 1 — API, desde la raíz del repositorio:**

```bash
.venv-dashboard/bin/python -m uvicorn api.dashboard_api:app --host 127.0.0.1 --port 8000 --env-file .env.dashboard.local
```

En PowerShell, sustituir el ejecutable por
`.\.venv-dashboard\Scripts\python.exe` y conservar el resto del comando.
El soporte dotenv se instala con `uvicorn[standard]`, ya incluido en los requisitos.

**Terminal 2 — web, desde `edev_models/app`:**

```bash
pnpm dev --hostname 127.0.0.1 --port 3000
```

Abrir [http://localhost:3000/](http://localhost:3000/). Mantener ambas terminales
abiertas. Para detener cada servicio, usar Ctrl+C en su terminal.
Esto no actualiza la web publicada: es una instancia en tu ordenador.

La configuración local desactiva la contraseña de equipo para facilitar la prueba.
**Ambos servicios deben permanecer limitados a loopback.** No usar `--host 0.0.0.0` ni `--hostname 0.0.0.0`,
no abrir puertos y no compartir esta instancia mediante túneles públicos.
En el servidor se mantiene la autenticación existente; no copiar allí este `.env`.

## 5. Comprobar que funciona

- [Sesión local](http://127.0.0.1:8000/session): con esta configuración devuelve
  `authenticated: true` y `auth_required: false`.
- [Estado de datos](http://127.0.0.1:8000/health): debe devolver `status: ok`.
- [Contador de pico local](http://127.0.0.1:8000/peak-accuracy?model=ensemble&days=30):
  devuelve fechas, aciertos y días evaluables/excluidos.
- La web debe indicar «Datos reales de producción». Revisar la fecha seleccionada.

La versión local no genera curvas, precios reales ni rankings de demostración.
Si una consulta falla o no tiene registros, muestra un estado explícito sin datos.
Los modelos del gráfico se descubren desde `predictions`; el selector de referencia
controla mínimos, medias y acierto de pico. El número de registros horarios es dinámico.

El ranking y la tarjeta de menor MAE leen directamente `model_metrics`, con el estado
de `models` unido por modelo y semilla. No recalculan MAE ni agrupan semillas.
El selector separa período, corte y supuestos BESS. Se puede ordenar por captura,
MAE o skill; no se otorga una insignia de adopción. El número de observaciones,
estado, semilla y período se muestran junto a los resultados.
No se interpreta `test_2026` ni `val_2025` como una ventana móvil o como el día del gráfico.

BESS lee `bess_plan` y `bess_result` del día seleccionado: no simula ni completa
potencia, eficiencia, ingresos o captura. Las tablas no tienen `source`, así que
la interfaz no atribuye esos registros a producción o test.

Inspección de solo lectura del 31/08/2026: 37 registros de modelos (todos retadores),
70 evaluaciones (33 test_2026 y 37 val_2025), ninguna prod_30d y ambas tablas BESS
vacías. Es una fotografía documental, no una constante usada por la app.

El encabezado muestra la fecha y hora de actualización de las predicciones del día
seleccionado, en Europe/Madrid. No es una ejecución verificada del cron ni un estado
de salud del pipeline. Se retiraron el indicador verde fijo y el hito BESS no verificado.

Estos cambios requieren **la API local nueva**: el contrato de `/leaderboard` y
`/bess/{fecha}` ha cambiado. Apuntar a la API publicada no basta; no se publicó nada.
El rol usado localmente necesita SELECT sobre `models`, `model_metrics`, `bess_plan`
y `bess_result`, además de `predictions` y `spot_price`. No ejecutar GRANT ni cambiar
el rol de producción como parte de estas pruebas.

### Jerarquía visual local

La navegación separa tres modos. **Predicción** es la entrada y contiene la curva,
la lectura diaria y el detalle horario. **Evaluación** contiene exclusivamente
resultados históricos de modelos. **BESS** tiene su propia fecha operativa y reúne
el plan, el SOC y el resultado económico; cambiar de modo no mezcla la evaluación
histórica con la fecha diaria seleccionada.

La previsión diaria usa un gráfico principal y una comparación destacada entre la
media prevista y la real sobre las mismas horas. La diferencia y la cobertura
emparejada se muestran de forma explícita; si todavía no existe precio real, quedan
pendientes y nunca se convierten en cero. Mínimo, máximo y acierto del pico forman
la segunda lectura. El selector controla el modelo de referencia; las series
visibles y la tabla horaria están plegadas para no competir con el gráfico.
Las horas de carga y descarga de `bess_plan` se superponen sobre la curva únicamente
cuando coinciden por instante y modelo con la predicción mostrada. No se reutiliza
el plan de Ensemble al seleccionar otro modelo sin plan guardado.

La evaluación histórica se presenta aparte. Un diagrama MAE–captura muestra una
marca por modelo y semilla, junto con tres indicadores y un ranking visual de cinco
filas que puede ordenarse por captura, MAE o skill. La tabla completa, el período y
los supuestos quedan en un detalle desplegable. El bloque BESS representa el plan
guardado mediante una línea temporal de carga, espera y descarga, un SOC escalonado
y un flujo económico de coste de carga, venta e ingreso neto. La comparación con el
oráculo del evaluador y el naive usa el resultado almacenado. BESS solo aparece
cuando sus tablas contienen filas para el día seleccionado; nunca se sustituye por
una simulación. Un resumen operativo encabeza la vista con las horas de carga y
descarga, el modelo y el ingreso previsto; no representa telemetría ni permite
accionar físicamente la batería.

## 6. Problemas habituales

- **`pnpm` no encontrado:** instalar la versión indicada mediante el procedimiento
  habitual del equipo. No hace falta reinstalar Node si ya cumple los requisitos.
- **Puerto ocupado:** detener tu instancia anterior. No matar procesos ajenos.
- **«Falta configurar la credencial team-auth»:** comprobar que se carga el archivo
  local y contiene `DASHBOARD_REQUIRE_AUTH=0`. No cambiar la configuración del VPS.
- **Error PostgreSQL:** revisar host, acceso de red, usuario, permisos y contraseña
  con el responsable. No incluir esos valores en un reporte de error público.
- **«Sin datos» o días excluidos:** puede faltar historial de producción o precios
  reales; no se rellenan con test ni se consideran automáticamente fallos del modelo.
- **Cambios de configuración sin efecto:** reiniciar el servicio local correspondiente.

## 7. Pruebas opcionales

Desde la raíz, con el Python del entorno virtual:

```bash
.venv-dashboard/bin/python -m unittest api.test_auth api.test_deployment_config api.test_peak_accuracy
```

Desde `app/`:

```bash
node --test lib/*.test.mjs
pnpm build
```

Estas pruebas unitarias no necesitan PostgreSQL real. La compilación no publica
nada. La comprobación TypeScript independiente todavía detecta errores heredados
en el tipado de respuestas JSON; no son un resultado de estas nuevas métricas.

## 8. Para quien prepara los archivos para el repositorio

En este ordenador `app/.git` conserva el historial separado de Sites. **No borrarlo
ni añadir `app/` como submódulo.** El repositorio raíz ya conoce `app/` como carpeta
por su antiguo `.gitkeep`; los archivos internos se pueden añadir explícitamente.
Los compañeros recibirán archivos normales de la web, sin ese `.git` anidado.

Desde la raíz, revisar primero el estado y simular esta selección:

```bash
git status --short
git add --dry-run -- .gitignore README.md .env.dashboard.example requirements-dashboard.txt api docs/dashboard_local.md docs/manual_despliegue_pulso_energia.md app/.env.example app/.gitignore app/.openai/hosting.json app/.oxfmtrc.json app/.oxlintrc.json app/app app/components app/hooks app/lib app/public app/components.json app/next.config.ts app/package.json app/pnpm-lock.yaml app/pnpm-workspace.yaml app/tsconfig.json app/vite.config.ts
```

Tras revisar la lista, repetir sin `--dry-run` para preparar esos archivos. No usar
`git add .`, `git add -A` ni añadir simplemente `app`: hay trabajos de modelos,
notebooks y datos que no pertenecen a esta entrega. Revisar especialmente los
cambios previos de `.gitignore`; no descartar trabajo ajeno.

Antes de crear un commit, inspeccionar `git diff --cached --stat`,
`git diff --cached --name-only` y `git ls-files --stage -- app`. Los archivos de
la web deben aparecer individualmente con modo `100644` o `100755`, nunca como
una sola entrada `160000 app` (submódulo). Revisar el diff completo localmente
para descartar secretos. `.gitignore` no elimina secretos ya versionados.

No incluir `.env.local`, `.env.dashboard.local`, `credentials.json`, claves,
`node_modules`, entornos virtuales, `dist`, `.wrangler`, archivos `.git` ni
`.pulso-local-backup*`. Las plantillas `.example` sí se incluyen.

El commit y push se harán únicamente sobre la rama acordada. Comprobar posibles
automatizaciones del repositorio antes del push: esta preparación no añade
despliegues ni ejecuta publicaciones. La guía y la selección por sí solas **no han
subido los cambios**; el equipo solo podrá descargarlos cuando se compartan en Git.
