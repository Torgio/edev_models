import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('installer',Path(__file__).with_name('install_bess_v2.py'))
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallerTests(unittest.TestCase):
    def execute_case(self,apply=False,fail=False):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            package=root/'package'
            target=root/'target'
            for path in [package/'scripts',package/'production/deploy',target/'scripts',target/'ingesta']:
                path.mkdir(parents=True,exist_ok=True)
            (package/'scripts/evaluar_diario.py').write_text('new')
            (target/'scripts/evaluar_diario.py').write_text('old')
            (target/'scripts/guardar_predicciones.py').write_text('')
            (target/'ingesta/config.py').write_text('')
            (package/'bess-manifest.json').write_text(json.dumps({'scripts/evaluar_diario.py':hashlib.sha256(b'new').hexdigest()}))
            def run(args,**kwargs):
                if args[0]=='pgrep':
                    return subprocess.CompletedProcess(args,1,'','')
                if fail and str(target/'scripts/evaluar_diario.py') in args:
                    raise subprocess.CalledProcessError(1,args)
                return subprocess.CompletedProcess(args,0,'','')
            argv=['installer','--target',str(target)] + (['--apply'] if apply else [])
            # Pick a fixed safe time independently of the time the suite runs.
            from datetime import datetime
            from zoneinfo import ZoneInfo
            class Clock:
                @staticmethod
                def now(tz): return datetime(2026,9,10,18,0,tzinfo=ZoneInfo('Europe/Madrid'))
            with patch.object(installer,'__file__',str(package/'production/deploy/install_bess_v2.py')), \
                 patch('sys.argv',argv),patch.object(installer.subprocess,'run',side_effect=run), \
                 patch.object(installer,'datetime',Clock):
                if fail:
                    with self.assertRaises(subprocess.CalledProcessError):installer.main()
                else:
                    installer.main()
            return (target/'scripts/evaluar_diario.py').read_text(),list((target/'.bess-backups').glob('*'))

    def test_check_does_not_install(self):
        contents,backups=self.execute_case()
        self.assertEqual(contents,'old')
        self.assertEqual(backups,[])
    def test_apply_installs_and_keeps_backup(self):
        contents,backups=self.execute_case(apply=True)
        self.assertEqual(contents,'new')
        self.assertEqual(len(backups),1)
    def test_failed_postcheck_rolls_back(self):
        contents,backups=self.execute_case(apply=True,fail=True)
        self.assertEqual(contents,'old')
        self.assertEqual(len(backups),1)

if __name__=='__main__':unittest.main()
