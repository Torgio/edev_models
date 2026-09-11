# Activación BESS v2

Paquete exclusivo de evaluación y planificación. No contiene credenciales, modelos
entrenados ni datos. No publica la web. Rutas esperadas: repositorio
`/home/ubuntu/scripts` y Python `/home/ubuntu/tfm-env/bin/python`.

Desde el Mac, en la raíz del repositorio:

```sh
scp pulso-bess-v2-update.tar.gz ubuntu@91.134.143.153:/home/ubuntu/
ssh ubuntu@91.134.143.153
```

En la sesión SSH:

```sh
mkdir -p /home/ubuntu/pulso-bess-v2-update
tar -xzf /home/ubuntu/pulso-bess-v2-update.tar.gz -C /home/ubuntu/pulso-bess-v2-update
/home/ubuntu/tfm-env/bin/python /home/ubuntu/pulso-bess-v2-update/production/deploy/install_bess_v2.py --apply
```

El instalador comprueba las dependencias y ejecuta el evaluador nuevo con
`--simulacro` en una transacción de solo lectura antes y después de copiar. Crea una
copia en `/home/ubuntu/scripts/.bess-backups/` y restaura los archivos si la
comprobación posterior falla. No modifica PostgreSQL durante la instalación.
Si las rutas reales difieren, usar `--target` y `--python` con las rutas correctas.
No activarlo mientras corren los pipelines: el instalador comprueba procesos y
rechaza las ventanas 11:25–11:50 y 13:25–13:40 de Madrid.

Comprobar la programación existente:

```sh
crontab -l | grep -E 'planificar_diario|evaluar_diario'
```

Deben existir una ejecución de planificación a las 11:35 y otra de evaluación a
las 13:30 con horario `Europe/Madrid`. Si no existen o están duplicadas, revisar la
configuración antes de añadir nada. El instalador no modifica ese archivo.

Para actualizar solo la ventana del ranking tras instalar (sin recalcular ni
sobrescribir planes históricos), puede ejecutarse el evaluador normal:

```sh
cd /home/ubuntu/scripts
/home/ubuntu/tfm-env/bin/python scripts/evaluar_diario.py
```

Esta orden **sí escribe** las métricas y las liquidaciones de planes v2 válidos.
Conserva los resultados BESS antiguos, que no se convierten a la nueva definición.
La instalación ya realiza la comprobación sin escritura; no hace falta volver a
planificar días pasados para rellenar datos.

Después del siguiente ciclo diario de planificación y evaluación:

```sh
/home/ubuntu/tfm-env/bin/python /home/ubuntu/scripts/scripts/verificar_bess_v2.py
```

Si se instala después de las 12:00 del 10 de septiembre, el primer plan que podrá
guardarse antes del corte será el objetivo 12 de septiembre, decidido el 11 a las
11:35, siempre que el cron y las predicciones funcionen. El verificador informa de
pendiente si aún no existe; no fabrica un plan para completar la comprobación.
