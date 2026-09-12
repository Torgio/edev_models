"""Construir paquete BESS a partir de una lista cerrada de archivos sin secretos."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import shutil

ROOT=Path(__file__).resolve().parents[2]
FILES=['modelos/bess_evaluation.py','modelos/tiempo_mercado.py','modelos/evaluar_modelos.py',
       'modelos/registrar_modelos.py','scripts/run_diario.py','scripts/planificar_diario.py',
       'scripts/evaluar_diario.py','scripts/verificar_bess_v2.py']
EXTRAS=['production/deploy/install_bess_v2.py','production/deploy/BESS_INSTALL.md']

def main():
    output=ROOT/'pulso-bess-v2-update.tar.gz'
    with tempfile.TemporaryDirectory() as temp:
        stage=Path(temp)
        manifest={}
        for name in FILES+EXTRAS:
            destination=stage/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,destination)
            if name in FILES:
                manifest[name]=hashlib.sha256(destination.read_bytes()).hexdigest()
        (stage/'bess-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        with tarfile.open(output,'w:gz') as archive:
            for name in FILES+EXTRAS+['bess-manifest.json']:
                archive.add(stage/name,arcname=name,recursive=False)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(f'{digest}  {output.name}\n')
    print(output)
    print('SHA256',digest)

if __name__=='__main__':main()
