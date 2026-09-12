"""Instalar solo el evaluador/planificador BESS; comprobacion por defecto, --apply activa."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target', type=Path, default=Path('/home/ubuntu/scripts'))
    ap.add_argument('--python', default='/home/ubuntu/tfm-env/bin/python')
    ap.add_argument('--apply', action='store_true')
    args=ap.parse_args()
    package=Path(__file__).resolve().parents[2]
    manifest=json.loads((package/'bess-manifest.json').read_text())
    paths=list(manifest)
    for name, expected in manifest.items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise SystemExit('Ruta de paquete invalida')
        if hashlib.sha256((package/name).read_bytes()).hexdigest()!=expected:
            raise SystemExit(f'Checksum incorrecto: {name}')
    for required in ('scripts/guardar_predicciones.py','ingesta/config.py'):
        if not (args.target/required).is_file():
            raise SystemExit(f'No se encuentra {required} en {args.target}; comprueba --target')
    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
             PYTHONPATH=os.pathsep.join([str(package/'modelos'),str(package/'scripts'),
                                       str(args.target/'ingesta'),str(args.target/'scripts')]))
    subprocess.run([args.python,'-c',
        "from bess_evaluation import optimizar, validar_plan; validar_plan(optimizar([0,100])); print('Motor fisico OK')"],
        env=env,check=True,cwd=package)
    # El evaluador de staging consulta la configuracion existente, nunca copia secretos.
    subprocess.run([args.python,str(package/'scripts/evaluar_diario.py'),'--simulacro'],
                   env=env,check=True,cwd=package)
    print('Archivos comprobados:', '\n'.join(paths), flush=True)
    if not args.apply:
        print('Comprobacion terminada. Para activar utiliza el mismo comando con --apply.')
        return
    now=datetime.now(ZoneInfo('Europe/Madrid'))
    if (now.hour==11 and 25<=now.minute<=50) or (now.hour==13 and 25<=now.minute<=40):
        raise SystemExit('Fuera de las ventanas 11:25–11:50 y 13:25–13:40 para no coincidir con cron.')
    running=subprocess.run(['pgrep','-af','[p]ython.*(evaluar_diario|planificar_diario|run_diario)\\.py'],
                           capture_output=True,text=True)
    if running.returncode not in (0,1):
        raise SystemExit('No se pudo comprobar si los pipelines estan ejecutandose')
    if running.returncode==0:
        raise SystemExit('Hay un evaluador o planificador ejecutandose; vuelve a intentar cuando termine.')
    backup=args.target/'.bess-backups'/now.strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True,exist_ok=False)
    existed={name:(args.target/name).exists() for name in paths}
    for name in paths:
        old=args.target/name
        if old.is_symlink():
            raise SystemExit(f'No se sustituye un enlace simbolico: {old}')
        if existed[name]:
            saved=backup/name
            saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(old,saved)
    (backup/'files.json').write_text(json.dumps(existed,indent=2))
    replaced=[]
    try:
        for name in paths:
            target=args.target/name
            target.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as f:
                staging=Path(f.name)
                f.write((package/name).read_bytes())
            staging.chmod(0o644)
            os.replace(staging,target)
            replaced.append(name)
        installed_env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([args.python,str(args.target/'scripts/evaluar_diario.py'),'--simulacro'],
                       env=installed_env,check=True,cwd=args.target)
    except BaseException:
        for name in reversed(replaced):
            if existed[name]:
                shutil.copy2(backup/name,args.target/name)
            else:
                (args.target/name).unlink()
        print('Fallo: restaurado el codigo anterior. Copia:',backup,flush=True)
        raise
    print('BESS v2 activado. Copia de seguridad:',backup)
    print('No se han cambiado cron, servicios, usuarios ni datos. El proximo cron utilizara el codigo nuevo.')

if __name__=='__main__':
    main()
