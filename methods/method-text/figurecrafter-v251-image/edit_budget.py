from pathlib import Path
import json,fcntl,datetime,os

def reserve(path,session,request,prompt_sha256,limit=3):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with os.fdopen(os.open(path,os.O_CREAT|os.O_RDWR,0o600),'r+') as f:
  fcntl.flock(f.fileno(),fcntl.LOCK_EX);f.seek(0);raw=f.read();data=json.loads(raw) if raw else {'limit':limit,'reservations':[]}
  if data['limit']!=limit:raise ValueError('Shared budget contract changed')
  if len(data['reservations'])>=limit:raise ValueError(f"Shared image edit budget exhausted: {limit}/{limit}")
  data['reservations'].append({'session':session,'request':request,'prompt_sha256':prompt_sha256,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'failed_or_abandoned_requests_count':True})
  f.seek(0);json.dump(data,f,ensure_ascii=False,indent=2);f.truncate();f.flush();fcntl.flock(f.fileno(),fcntl.LOCK_UN)
 return len(data['reservations'])
