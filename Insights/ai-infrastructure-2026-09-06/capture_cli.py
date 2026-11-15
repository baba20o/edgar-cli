import os,sys,json,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
repo=Path('/mnt/c/projects/edgar-cli')
out=repo/'Insights/ai-infrastructure-2026-09-06'
label,*args=sys.argv[1:]
if not label or '/' in label: raise SystemExit('safe label required')
env=os.environ.copy();env['PYTHONPATH']='/tmp/uspto-exploration-deps:/tmp/edgar-research-cli-base:'+str(repo)
env['NO_COLOR']='1';env['PYTHONIOENCODING']='utf-8'
started=datetime.now(timezone.utc).isoformat(); t=time.monotonic()
try:
 p=subprocess.run([sys.executable,'-m','edgar',*args],cwd=repo,env=env,capture_output=True,timeout=150)
 stdout=p.stdout.decode('utf-8',errors='replace');stderr=p.stderr.decode('utf-8',errors='replace');code=p.returncode
except subprocess.TimeoutExpired as e:
 stdout=(e.stdout or b'').decode('utf-8',errors='replace');stderr=(e.stderr or b'').decode('utf-8',errors='replace')+'\nCAPTURE: outer 150-second timeout';code=124
try: data=json.loads(stdout);parsed=True
except ValueError: data=None;parsed=False
fname=label+('.json' if parsed else '.txt')
(out/fname).write_text(stdout);(out/(label+'.stderr.txt')).write_text(stderr)
manifest_path=out/'MANIFEST.json'
m=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'method':'Actual CLI subprocess python3 -m edgar. Canonical argv recorded. No application code modified. Default repository SEC User-Agent. Sequential calls; --no-cache when shown. Supplemental web research is separate.','calls':[]}
row={'label':label,'argv':['edgar',*args],'started_at_utc':started,'elapsed_seconds':round(time.monotonic()-t,3),'exit_code':code,'stdout':fname,'stderr':label+'.stderr.txt','json_parseable':parsed}
m['calls'].append(row);manifest_path.write_text(json.dumps(m,indent=2)+'\n')
print(json.dumps(row))
if isinstance(data,dict):
 print('keys:',list(data));print('summary:',json.dumps(data,ensure_ascii=False)[:4500])
else: print(stdout[:1200])
if stderr: print('stderr:',stderr[:1500])
sys.exit(code)
