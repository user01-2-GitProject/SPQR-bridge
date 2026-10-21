import hashlib,json,os,shutil,subprocess,tempfile
from pathlib import Path
source=Path.cwd()
root=Path(tempfile.mkdtemp(prefix='spqr-isolated-baseline-'))
files=[p for folder in ['src','tests'] for p in (source/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'live_fixture' not in p.parts]
files += [source/'pyproject.toml',source/'uv.lock',source/'README.md']
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
manifest={str(p.relative_to(source)):digest(p) for p in files}
local_before=digest(source/'config/bridge.local.toml')
for p in files:
    dst=root/p.relative_to(source);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
(root/'config').mkdir()
shutil.copy2(source/'config/bridge.example.toml',root/'config/bridge.example.toml')
(root/'config/bridge.local.toml').write_text((source/'config/bridge.example.toml').read_text().replace('/ABSOLUTE/PATH/TO/SPQR',str(root)))
(root/'baseline-manifest.json').write_text(json.dumps(manifest,indent=2))
env=os.environ.copy()
for key in ['SPQR_LIVE_SMOKE','SPQR_BRIDGE_CONFIG','PYTHONPATH','VIRTUAL_ENV']:env.pop(key,None)
print('ISOLATED_ROOT',root,flush=True)
setup=subprocess.run(['uv','sync','--offline','--frozen','--python',str(source/'.venv/bin/python')],cwd=root,env=env,capture_output=True,text=True)
(root/'setup-output.txt').write_text(setup.stdout+setup.stderr)
print('SETUP_EXIT',setup.returncode,flush=True)
if setup.returncode:print(setup.stderr);raise SystemExit(setup.returncode)
probe=subprocess.run([str(root/'.venv/bin/python'),'-c','import spqr_bridge;print(spqr_bridge.__file__)'],cwd=root,env=env,capture_output=True,text=True)
print('IMPORT',probe.stdout.strip(),flush=True)
assert str(root) in probe.stdout
run=subprocess.run([str(root/'.venv/bin/python'),'-m','pytest','-q','-p','no:cacheprovider','--tb=short','--junitxml='+str(root/'baseline-junit.xml')],cwd=root,env=env,capture_output=True,text=True)
(root/'baseline-output.txt').write_text(run.stdout+run.stderr)
print(run.stdout+run.stderr,flush=True)
assert all(digest(source/name)==h and digest(root/name)==h for name,h in manifest.items())
assert digest(source/'config/bridge.local.toml')==local_before
print('MANIFEST_MATCH',len(manifest),'REAL_CONFIG_UNCHANGED',True,flush=True)
(root/'verification.json').write_text(json.dumps({'exit_code':run.returncode,'manifest_files':len(manifest),'source_copy_match':True,'real_local_config_unchanged':True,'import':probe.stdout.strip()},indent=2))
raise SystemExit(run.returncode)
