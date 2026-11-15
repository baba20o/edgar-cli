from pathlib import Path
import requests,json,hashlib,sys
from datetime import datetime,timezone
sys.path[:0]=['/tmp/uspto-exploration-deps','/tmp/edgar-research-cli-base','/mnt/c/projects/edgar-cli']
from edgar.api import DEFAULT_USER_AGENT
from bs4 import BeautifulSoup
out=Path('/mnt/c/projects/edgar-cli/Insights/ai-infrastructure-2026-09-06')
entries=[]
for label,manifest in [('msft','01-msft-filings.json'),('nvda','02-nvda-filings.json'),('vrt','03-vrt-filings.json')]:
 d=json.loads((out/manifest).read_text());f=d['filings'][0];url=f['primary_doc_url']
 assert url.startswith('https://www.sec.gov/Archives/edgar/data/')
 resp=requests.get(url,headers={'User-Agent':DEFAULT_USER_AGENT},timeout=(10,40),stream=True);resp.raise_for_status()
 chunks=[];size=0
 for c in resp.iter_content(65536):
  size+=len(c)
  if size>12*1024*1024: raise RuntimeError('12 MiB document limit')
  chunks.append(c)
 content=b''.join(chunks);p=out/'documents'/f'{label}-direct.html';p.write_bytes(content)
 soup=BeautifulSoup(content,'html.parser')
 for x in soup(['script','style']):x.decompose()
 txt=soup.get_text(' ',strip=True);tp=p.with_suffix('.txt');tp.write_text(txt)
 entries.append({'label':label,'method':'Supplemental direct SEC primary_doc_url download, NOT CLI exhibits download. BeautifulSoup text extraction, script/style excluded.','source_url':url,'retrieved_at_utc':datetime.now(timezone.utc).isoformat(),'file':str(p.relative_to(out)),'text_file':str(tp.relative_to(out)),'bytes':size,'sha256':hashlib.sha256(content).hexdigest(),'text_sha256':hashlib.sha256(tp.read_bytes()).hexdigest()})
 print(label,size,len(txt),flush=True)
(out/'DOCUMENTS.json').write_text(json.dumps(entries,indent=2)+'\n')
