"""Offline wheel install/import/CLI check, reusing the already verified dependencies."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from market_research.storage import write_json

ROOT=Path(__file__).resolve().parents[1]


def main():
    wheel=sorted((ROOT/'dist/stage5').glob('*.whl'))[-1]
    with tempfile.TemporaryDirectory(prefix='skt-ia-stage5-wheel-') as td:
        installed=subprocess.run([sys.executable,'-m','pip','install','--no-deps','--target',td,str(wheel)],text=True,capture_output=True)
        if installed.returncode:raise RuntimeError('offline_wheel_install_failed')
        source='''import sys, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from market_research import cli
assert cli.__file__.startswith(sys.argv[1]+'/')
code=cli.main(['training-config-check','--db','tests/generated/stage5/signal/pit.sqlite','--dataset-db','tests/generated/stage5/signal/datasets.sqlite','--mode','synthetic','--config','tests/generated/stage5/signal/regression.json','--output','data/stage5/release/wheel-smoke-check.json'])
assert code==0
loaded={name:m.__file__ for name,m in sys.modules.items() if name.startswith('market_research') and getattr(m,'__file__',None)}
assert all(path.startswith(sys.argv[1]+'/') for path in loaded.values())
from market_research.storage import write_json
write_json('data/stage5/release/wheel-import-check.json',{'status':'PASS','installed_from_local_wheel':True,'dependencies_reused_from_verified_venv':True,'cli_exit':code,'imported_project_modules':len(loaded),'source_imports_mixed':False})
'''
        checked=subprocess.run([sys.executable,'-c',source,td],cwd=ROOT,text=True,capture_output=True)
        (ROOT/'data/stage5/release/wheel-cli-smoke.log').write_text(checked.stdout+'\n'+checked.stderr)
        if checked.returncode:raise RuntimeError('wheel_cli_or_import_provenance_failed')
    print(json.dumps(json.loads((ROOT/'data/stage5/release/wheel-import-check.json').read_text())))


if __name__=='__main__':main()
