"""Current offline tests, wheel/source/import checks and protected-data inventory.
Actual one-shot requests/results are recorded separately; this does not download.
"""
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from market_research.storage import sha256,write_json,code_version
from market_research.http import utc_now

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/stage5_5'


def measured(command,log):
    log=log.resolve()
    started=time.monotonic()
    with log.open('w') as f:
        child=subprocess.Popen(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT)
        _,status,usage=os.wait4(child.pid,0)
        child.returncode=os.waitstatus_to_exitcode(status)
    return {'command':command,'exit_code':child.returncode,'elapsed_seconds':time.monotonic()-started,'max_rss_kib':usage.ru_maxrss,'log':str(log.relative_to(ROOT))}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    result={'started_at':utc_now(),'environment':{'python':sys.version,'machine':platform.machine(),'platform':platform.platform(),
        'dependencies':{name:importlib.metadata.version(name) for name in ['numpy','scipy','pandas','scikit-learn','exchange_calendars']}}}
    result['tests']=measured([sys.executable,'-m','unittest','discover','-s','tests','-v'],OUT/'all_tests.log')
    text=(OUT/'all_tests.log').read_text();m=re.search(r'Ran (\d+) tests',text)
    result['tests'].update(count=int(m[1]) if m else None,passed=result['tests']['exit_code']==0,known_warnings='ResourceWarning' in text)
    build=measured(['python3','-c','from setuptools.build_meta import build_wheel; build_wheel("dist/stage5_5")'],OUT/'build.log')
    wheel=sorted((ROOT/'dist/stage5_5').glob('*.whl'))[-1];matched=[]
    with zipfile.ZipFile(wheel) as archive:
        for path in sorted((ROOT/'src/market_research').rglob('*.py')):
            name=str(path.relative_to(ROOT/'src'))
            if archive.read(name)!=path.read_bytes(): raise ValueError('wheel_source_mismatch:'+name)
            matched.append(name)
    build.update(module_count=len(matched),source_equal=True,wheel=str(wheel.relative_to(ROOT)),sha256=sha256(wheel.read_bytes()))
    with tempfile.TemporaryDirectory() as td:
        install=measured([sys.executable,'-m','pip','install','--no-deps','--target',td,str(wheel)],OUT/'wheel_install.log')
        smoke='''import sys,importlib,pkgutil,json
sys.path.insert(0,sys.argv[1])
import market_research
mods=[importlib.import_module(x.name) for x in pkgutil.walk_packages(market_research.__path__,market_research.__name__+'.')]
assert all(m.__file__.startswith(sys.argv[1]+'/') for m in [market_research]+mods)
from market_research.cli import main
assert main(['observed-config-check','--config','configs/observed_ibm_demo.json'])==0
print(json.dumps({'imported':len(mods)+1,'source_imports_mixed':False}))
'''
        smoke_result=measured([sys.executable,'-c',smoke,td],OUT/'wheel_smoke.log')
        build.update(offline_install=install,offline_import_and_cli=smoke_result)
    result['build']=build
    result['pip_check']=measured([sys.executable,'-m','pip','check'],OUT/'pip_check.log')
    result['diff_check']=measured(['git','diff','--check'],OUT/'diff_check.log')
    original=json.loads((OUT/'starting_state.json').read_text())['protected_files']
    changed=[name for name,digest in original.items() if not (ROOT/name).is_file() or sha256((ROOT/name).read_bytes())!=digest]
    result['protected_existing_data']={'files_checked':len(original),'changed':changed,'unchanged':not changed}
    result.update(finished_at=utc_now(),code_sha256=code_version(ROOT))
    write_json(OUT/'verification.json',result)
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result['tests']['passed'] and all(result[k]['exit_code']==0 for k in ('pip_check','diff_check')) and build['exit_code']==0 and build['offline_import_and_cli']['exit_code']==0 and not changed else 1


if __name__=='__main__':sys.exit(main())
