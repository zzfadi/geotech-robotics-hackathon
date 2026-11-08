"""Tailnet Dream debugger and command queue. Worker credentials never reach the UI."""
import argparse,base64,json,re,secrets,threading,time,uuid
from urllib.parse import parse_qs,urlsplit
from collections import deque
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from fleet_planning import build_plan
from prompt_routing import parse_target

class Store:
    def __init__(self,path):
        self.path=path;path.mkdir(parents=True,exist_ok=True)
        key=path/'worker-token'
        if not key.exists():key.write_text(secrets.token_urlsafe(32))
        self.token=key.read_text().strip();self.events=deque(maxlen=200);self.command=None;self.voice=None;self.worker_at=0;self.preview=deque(maxlen=300);self.preview_request=None;self.live_frame=None;self.recording=None;self.lock=threading.RLock()
        # Replica-table fleet (DreamSim/fleet_worker.py --serve). Shares the
        # editor with the main worker, so the two command queues exclude each other.
        self.fleet={'command':None,'worker_at':0,'tables':[],'status':None,'frames':{},'frame_times':{},'recording':None,'expected_tables':list(range(1,11)),'worker_id':None,'preview_enabled':False}
        if (path/'fleet-recording.json').exists():
            try:
                recording=json.loads((path/'fleet-recording.json').read_text())
                if re.fullmatch(r'[0-9a-f]{32}',recording.get('request_id','')):
                    directory=path/'recordings'/('fleet-'+recording['request_id'])
                    recording['recorded_tables']=[i for i in recording.get('tables',[]) if type(i) is int and (directory/('T%02d.mp4'%i)).is_file()]
                    self.fleet['recording']=recording
            except ValueError:pass
        if (path/'recording.json').exists():
            self.recording=json.loads((path/'recording.json').read_text())
            # Preserve existing deployed clips when moving to request-specific URLs.
            request_id=self.recording.get('request_id','')
            if re.fullmatch(r'[0-9a-f]{32}',request_id):
                directory=path/'recordings'/request_id;directory.mkdir(parents=True,exist_ok=True)
                for role in self.recording.get('roles',['overhead']):
                    if role not in ('overhead','wrist_l','wrist_r'):continue
                    legacy=path/('recording-'+role+'.mp4')
                    if role=='overhead' and not legacy.exists():legacy=path/'recording.mp4'
                    target=directory/(role+'.mp4')
                    if legacy.exists() and not target.exists():target.write_bytes(legacy.read_bytes())
        log=path/'events.jsonl'
        if log.exists():
            for line in log.read_text().splitlines():
                try:self.events.append(json.loads(line))
                except ValueError:continue
    def event(self,data):
        allowed={'kind','request_id','prompt','message','elapsed_seconds','state','frames','action','cycle','source'}
        event={k:v for k,v in data.items() if k in allowed};event['at']=time.time();event['id']=uuid.uuid4().hex
        if not isinstance(event.get('kind'),str):raise ValueError('Missing event kind')
        with self.lock:
            self.events.append(event)
            log=self.path/'events.jsonl'
            if log.exists() and log.stat().st_size>10_000_000:log.replace(self.path/'events.previous.jsonl')
            with log.open('a') as f:f.write(json.dumps(event,allow_nan=False)+'\n')
        return event

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,status,data,mime='application/json'):
        body=json.dumps(data,allow_nan=False).encode() if mime=='application/json' else data
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
    def send_video(self,path):
        """Serve a stable recording with native browser seeking support."""
        if not path.is_file():return self.send(404,{'error':'Recording unavailable'})
        body=path.read_bytes();size=len(body);start=0;end=size-1;status=200
        requested=self.headers.get('Range')
        if requested:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested.strip())
            try:
                if not match or not any(match.groups()):raise ValueError()
                first,last=match.groups()
                if first:
                    start=int(first);end=min(int(last),size-1) if last else size-1
                else:
                    suffix=int(last)
                    if suffix<=0:raise ValueError()
                    start=max(0,size-suffix)
                if start>=size or start>end:raise ValueError()
            except ValueError:
                self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return
            body=body[start:end+1];status=206
        self.send_response(status);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(body)))
        self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def worker(self):return secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.store.token)
    def fleet_get(self,route):
        s=self.server.store;f=s.fleet
        if route.path=='/api/fleet':
            with s.lock:return self.send(200,{'online':time.time()-f['worker_at']<15,'tables':[{**{'index':i,'name':'Awaiting worker','ready':False,'error':'Dream session not ready'},**next((t for t in f['tables'] if t['index']==i),{})} for i in f['expected_tables']],'command':f['command'],
                                              'status':f['status'],'recording':f['recording'],'expected_tables':f['expected_tables'],'preview_enabled':f['preview_enabled'],'server_time':time.time()})
        if route.path=='/api/fleet/frames':
            with s.lock:return self.send(200,{'frames':f['frames'],'received_at':f['frame_times'],'server_time':time.time()})
        match=re.fullmatch(r'/api/fleet/recording/([0-9a-f]{32})/(T\d\d)\.mp4',route.path)
        if match:return self.send_video(s.path/'recordings'/('fleet-'+match[1])/(match[2]+'.mp4'))
        if route.path=='/api/fleet/worker/next':
            if not self.worker():return self.send(401,{'error':'Worker authentication required'})
            with s.lock:
                worker_id=parse_qs(route.query).get('worker_id',[''])[0]
                if not worker_id or worker_id!=f.get('worker_id'):return self.send(409,{'error':'Fleet worker lease required'})
                f['worker_at']=time.time();command=f['command']
                if command and command['status']=='queued':
                    command['status']='running';command['claimed_by']=worker_id
                    return self.send(200,{'command':command,'preview_enabled':f['preview_enabled']})
                return self.send(200,{'command':None,'preview_enabled':f['preview_enabled']})
        return self.send(404,{'error':'Not found'})
    def fleet_post(self,data):
        s=self.server.store;f=s.fleet
        if self.path in ('/api/fleet/command','/api/fleet/plan'):
            with s.lock:
                known=f['expected_tables']
                tables=known if data.get('tables')=='all' else data.get('tables')
                if not isinstance(tables,list) or not tables or any(type(t) is not int or t not in known for t in tables) or len(set(tables))!=len(tables):
                    raise ValueError('Select one or more replica tables')
                prompt=data.get('prompt') or ''
                if not isinstance(prompt,str):raise ValueError('Prompt must be text')
                prompt=prompt.strip()
                if len(prompt)>1000:raise ValueError('Prompt must be at most 1000 characters')
                mode=data.get('mode', 'same' if prompt else 'configured')
                plan=build_plan(tables,mode=mode,prompt=prompt,seed=data.get('seed'),randomize=data.get('randomize',False))
                if self.path=='/api/fleet/plan':return self.send(200,plan)
                if time.time()-f['worker_at']>15:return self.send(503,{'error':'Fleet worker is offline'})
                ready={t['index'] for t in f['tables'] if t.get('ready',True)}
                unavailable=[i for i in tables if i not in ready]
                if unavailable:return self.send(503,{'error':'Dream sessions unavailable for '+', '.join('Table %d'%(i+1) for i in unavailable)+'. No tables were started.'})
                if f['command'] and f['command']['status'] in ('queued','running'):return self.send(409,{'error':'A replica-table run is already active'})
                if s.command and s.command['status'] in ('queued','running'):return self.send(409,{'error':'The main table (T00) is running; wait for it to finish'})
                f['command']={'id':uuid.uuid4().hex,'tables':sorted(tables),'prompt':prompt,'plan':plan,'status':'queued','stop':False,'created_at':time.time()}
                f['status']=None;f['frames']={};f['frame_times']={}
                s.event({'kind':'fleet.queued','request_id':f['command']['id'],'prompt':prompt or 'each table uses its configured prompt',
                         'message':'Replica tables '+', '.join('T%02d'%t for t in f['command']['tables'])+' queued to run in parallel'})
                return self.send(202,f['command'])
        if self.path=='/api/fleet/preview':
            if type(data.get('enabled')) is not bool:raise ValueError('Expected enabled boolean')
            with s.lock:f['preview_enabled']=data['enabled']
            return self.send(200,{'enabled':f['preview_enabled']})
        if self.path=='/api/fleet/stop':
            with s.lock:
                if f['command']:f['command']['stop']=True
            return self.send(200,{'stopping':True})
        if not self.worker():return self.send(401,{'error':'Worker authentication required'})
        with s.lock:
            command=f['command']
            worker_id=data.get('worker_id','')
            if not isinstance(worker_id,str) or not re.fullmatch(r'[0-9a-f]{32}',worker_id):return self.send(409,{'error':'Fleet worker_id required'})
            if self.path=='/api/fleet/worker/hello':
                if f.get('worker_id') not in (None,worker_id) and (time.time()-f['worker_at']<15 or (command and command['status'] in ('queued','running'))):return self.send(409,{'error':'Another fleet worker owns the lease'})
                tables=data.get('tables')
                if (not isinstance(tables,list) or len(tables)>12 or any(not isinstance(t,dict) or type(t.get('index')) is not int or not 1<=t['index']<=12 for t in tables) or len({t['index'] for t in tables})!=len(tables)):
                    raise ValueError('Expected distinct fleet table IDs')
                expected=data.get('expected_tables',list(range(1,11)))
                if not isinstance(expected,list) or not expected or len(set(expected))!=len(expected) or any(type(i) is not int or not 1<=i<=12 for i in expected):raise ValueError('Invalid expected table IDs')
                f['worker_id']=worker_id;f['worker_at']=time.time()
                f['expected_tables']=sorted(expected)
                f['tables']=[dict({k:t.get(k) for k in ('index','name','prompt','checkpoint','position_ik','pad_contacts','action_rows','cycles','error')},ready=t.get('ready',True)) for t in tables]
                return self.send(200,{'ok':True})
            if self.path=='/api/fleet/worker/preview':
                if worker_id!=f.get('worker_id'):return self.send(409,{'error':'Fleet worker lease required'})
                frames=data.get('frames') or {};capture_times=data.get('frame_times') or {}
                for key,value in frames.items():
                    raw=base64.b64decode(value,validate=True)
                    if key not in ['T%02d'%i for i in f['expected_tables']] or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9') or len(raw)>200_000:raise ValueError('Expected table JPEG frames')
                    captured=capture_times.get(key)
                    if not isinstance(captured,(float,int)) or not 0<captured<=time.time()+5:raise ValueError('Expected actual capture timestamp')
                    f['frames'][key]=value;f['frame_times'][key]=captured
                f['worker_at']=time.time()
                return self.send(200,{'ok':True,'preview_enabled':f['preview_enabled']})
            if worker_id!=f.get('worker_id') or (command and command.get('claimed_by')!=worker_id):return self.send(409,{'error':'Fleet command owned by another worker'})
            f['worker_at']=time.time()
            if self.path=='/api/fleet/worker/status':
                if not command or data.get('request_id')!=command['id']:raise ValueError('Unknown fleet request')
                frames={}
                for key,value in (data.get('frames') or {}).items():
                    raw=base64.b64decode(value,validate=True)
                    if key not in ['T%02d'%i for i in command['tables']] or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9') or len(raw)>200_000:raise ValueError('Expected table JPEG frames')
                    frames[key]=value
                capture_times=data.get('frame_times') or {}
                for key,value in frames.items():
                    captured=capture_times.get(key)
                    if isinstance(captured,(float,int)) and 0<captured<=time.time()+5:f['frame_times'][key]=captured
                    elif value!=f['frames'].get(key):f['frame_times'][key]=time.time()
                f['frames'].update(frames)
                f['status']={'request_id':command['id'],'phase':str(data.get('phase','')),'tables':data.get('tables') or {},'updated':time.time()}
                return self.send(200,{'ok':True,'stop':command['stop']})
            if self.path=='/api/fleet/worker/video':
                if not command or data.get('request_id')!=command['id'] or data.get('table') not in ['T%02d'%i for i in command['tables']]:raise ValueError('Unknown fleet recording')
                raw=base64.b64decode(data['video'],validate=True)
                if not 12<len(raw)<1_400_000 or raw[4:8]!=b'ftyp':raise ValueError('Expected bounded MP4 recording')
                directory=s.path/'recordings'/('fleet-'+command['id']);directory.mkdir(parents=True,exist_ok=True)
                target=directory/(data['table']+'.mp4')
                if target.exists() and target.read_bytes()!=raw:return self.send(409,{'error':'Recording already uploaded'})
                if not target.exists():
                    tmp=directory/(data['table']+'.tmp');tmp.write_bytes(raw);tmp.replace(target)
                return self.send(200,{'ok':True})
            if self.path=='/api/fleet/worker/done':
                if not command or data.get('request_id')!=command['id']:raise ValueError('Unknown fleet request')
                command['status']=data.get('status','done') if data.get('status') in ('done','failed','stopped') else 'done'
                results=data.get('results') or []
                if not isinstance(results,list):raise ValueError('Expected per-table results')
                by_table={r.get('table'):r for r in results if isinstance(r,dict)}
                results=[by_table.get('T%02d'%i,{'table':'T%02d'%i,'outcome':'error','error':'Worker returned no result for this table'}) for i in command['tables']]
                directory=s.path/'recordings'/('fleet-'+command['id'])
                recorded=[i for i in command['tables'] if (directory/('T%02d.mp4'%i)).is_file()]
                f['recording']={'request_id':command['id'],'tables':command['tables'],'recorded_tables':recorded,'prompt':command['prompt'],'plan':command.get('plan'),'status':command['status'],'results':results,'finished':time.time()}
                (s.path/'fleet-recording.json').write_text(json.dumps(f['recording']))
                return self.send(200,{'ok':True})
        return self.send(404,{'error':'Not found'})
    def do_GET(self):
        s=self.server.store
        if self.path=='/':return self.send(200,(Path(__file__).parent/'web/index.html').read_bytes(),'text/html; charset=utf-8')
        if self.path.startswith('/api/fleet'):return self.fleet_get(urlsplit(self.path))
        if self.path=='/api/live':
            with s.lock:return self.send(200,{'frame':s.live_frame,'count':len(s.preview),'server_time':time.time()})
        if self.path.startswith('/api/replay/'):
            try:
                index=int(self.path.rsplit('/',1)[1])
                with s.lock:
                    if not 0<=index<len(s.preview):return self.send(404,{'error':'Frame unavailable'})
                    return self.send(200,{'frame':s.preview[index],'count':len(s.preview),'server_time':time.time()})
            except ValueError:return self.send(400,{'error':'Invalid frame index'})
        route=urlsplit(self.path)
        if route.path.startswith('/api/recording/') or route.path=='/api/recording.mp4':
            role=route.path.rsplit('/',1)[-1].removesuffix('.mp4') if route.path.startswith('/api/recording/') else 'overhead'
            if role not in ('overhead','wrist_l','wrist_r'):return self.send(404,{'error':'Unknown camera'})
            with s.lock:
                request_id=parse_qs(route.query).get('id',[(s.recording or {}).get('request_id','')])[0]
            if not re.fullmatch(r'[0-9a-f]{32}',request_id):return self.send(404,{'error':'Unknown recording'})
            return self.send_video(s.path/'recordings'/request_id/(role+'.mp4'))
        if self.path=='/api/status':
            with s.lock:return self.send(200,{'events':[{k:v for k,v in e.items() if k!='frames'} for e in s.events],'recording':s.recording,'command':s.command,'voice':({k:v for k,v in s.voice.items() if k!='audio'} if s.voice else None),'worker_online':time.time()-s.worker_at<10,'server_time':time.time()})
        if self.path=='/api/worker/next':
            if not self.worker():return self.send(401,{'error':'Worker authentication required'})
            with s.lock:
                s.worker_at=time.time()
                command=s.command
                if command and command['status']=='queued':command['status']='running'
                return self.send(200,{'command':command,'voice':s.voice if s.voice and s.voice['status']=='queued' else None})
        return self.send(404,{'error':'Not found'})
    def do_POST(self):
        s=self.server.store
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=2_000_000:return self.send(413,{'error':'Invalid body size'})
            if self.headers.get_content_type()!='application/json':return self.send(415,{'error':'JSON required'})
            origin=self.headers.get('Origin')
            if origin and origin not in ('http://'+self.headers.get('Host',''),'https://'+self.headers.get('Host','')):return self.send(403,{'error':'Cross-origin request refused'})
            data=json.loads(self.rfile.read(length))
            if self.path=='/api/route':return self.send(200,parse_target(data.get('prompt','')))
            if self.path.startswith('/api/fleet/'):return self.fleet_post(data)
            if self.path=='/api/worker/video':
                if not self.worker():return self.send(401,{'error':'Worker authentication required'})
                raw=base64.b64decode(data['video'],validate=True)
                if not 12<len(raw)<1_400_000 or raw[4:8]!=b'ftyp':raise ValueError('Expected bounded MP4 recording')
                with s.lock:
                    if not any(e.get('request_id')==data.get('request_id') for e in s.events):raise ValueError('Unknown recording request')
                    role=data.get('role','overhead')
                    if role not in ('overhead','wrist_l','wrist_r'):raise ValueError('Unknown camera')
                    request_id=data['request_id']
                    if not re.fullmatch(r'[0-9a-f]{32}',request_id):raise ValueError('Invalid recording request')
                    directory=s.path/'recordings'/request_id;directory.mkdir(parents=True,exist_ok=True)
                    target=directory/(role+'.mp4')
                    # A request/role URL is immutable, so a browser can seek safely.
                    if target.exists() and target.read_bytes()!=raw:return self.send(409,{'error':'Recording already uploaded'})
                    if not target.exists():
                        tmp=directory/(role+'.tmp');tmp.write_bytes(raw);tmp.replace(target)
                    roles=list(s.recording.get('roles',[])) if s.recording and s.recording['request_id']==data['request_id'] else []
                    if role not in roles:roles.append(role)
                    s.recording={k:data[k] for k in ('request_id','prompt','frames','result')}
                    s.recording['roles']=roles
                    (s.path/'recording.json').write_text(json.dumps(s.recording))
                return self.send(200,{'ok':True})
            if self.path=='/api/worker/frame':
                if not self.worker():return self.send(401,{'error':'Worker authentication required'})
                frames=data.get('frames',{})
                if set(frames)!={'video.overhead','video.wrist_l','video.wrist_r'}:raise ValueError('Expected all three camera views')
                for value in frames.values():
                    raw=base64.b64decode(value,validate=True)
                    if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):raise ValueError('Expected JPEG frame')
                captured=float(data['captured_unix'])
                if not 0<=time.time()-captured<10:raise ValueError('Frame is not fresh')
                with s.lock:
                    recording=data.get('recording',True)
                    if recording and (not s.command or data.get('request_id')!=s.command['id']):raise ValueError('Unknown frame request')
                    if recording and s.preview_request!=data['request_id']:
                        s.preview.clear();s.preview_request=data['request_id']
                    frame={'request_id':data['request_id'],'sequence':data['sequence'],'captured_unix':captured,'frames':frames}
                    s.live_frame=frame
                    if recording:s.preview.append(frame)
                return self.send(200,{'ok':True})
            if self.path=='/api/voice':
                audio=base64.b64decode(data.get('audio',''),validate=True)
                if not 100<len(audio)<=1_400_000:raise ValueError('Record a short voice command (maximum 30 seconds)')
                with s.lock:
                    if time.time()-s.worker_at>10:return self.send(503,{'error':'Voice worker is offline'})
                    if s.voice and s.voice['status']=='queued':return self.send(409,{'error':'Voice transcription is busy'})
                    s.voice={'id':uuid.uuid4().hex,'status':'queued','audio':data['audio'],'created_at':time.time()}
                    s.event({'kind':'voice.queued','message':'Voice command received for local transcription'})
                    return self.send(202,{'id':s.voice['id']})
            if self.path=='/api/worker/transcript':
                if not self.worker():return self.send(401,{'error':'Worker authentication required'})
                with s.lock:
                    if not s.voice or s.voice['id']!=data.get('id'):raise ValueError('Unknown voice request')
                    s.voice={'id':s.voice['id'],'status':'failed' if data.get('error') else 'done','text':data.get('text',''),'error':data.get('error')}
                    s.event({'kind':'voice.transcribed' if not data.get('error') else 'voice.failed','message':data.get('error') or data.get('text','')})
                    return self.send(200,{'ok':True})
            if self.path=='/api/command':
                route=parse_target(data.get('prompt',''))
                if route['route']=='fleet':
                    self.path='/api/fleet/command'
                    return self.fleet_post({'tables':route['tables'],'prompt':route['prompt'],'mode':'same'})
                prompt=route['prompt']
                if not 1<=len(prompt)<=1000:raise ValueError('Enter a prompt of 1–1000 characters')
                with s.lock:
                    if s.command and s.command['status'] in ('queued','running'):return self.send(409,{'error':'A command is already active'})
                    if s.fleet['command'] and s.fleet['command']['status'] in ('queued','running'):return self.send(409,{'error':'Replica tables are running; wait for that run or stop it'})
                    if time.time()-s.worker_at>10:return self.send(503,{'error':'Unreal worker is offline'})
                    s.command={'id':uuid.uuid4().hex,'prompt':prompt,'status':'queued','stop':False,'created_at':time.time()}
                    s.event({'kind':'command.queued','request_id':s.command['id'],'prompt':prompt,'source':data.get('source','text')})
                    return self.send(202,s.command)
            if self.path=='/api/stop':
                with s.lock:
                    if s.command:s.command['stop']=True
                return self.send(200,{'stopping':True})
            if self.path in ('/api/events','/api/worker/done'):
                if not self.worker():return self.send(401,{'error':'Worker authentication required'})
                if self.path=='/api/events':return self.send(200,s.event(data))
                with s.lock:
                    if s.command and s.command['id']==data.get('request_id'):s.command['status']=data.get('status','done')
                return self.send(200,{'ok':True})
            return self.send(404,{'error':'Not found'})
        except (ValueError,TypeError,KeyError) as error:self.send(400,{'error':str(error)})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bind',default='127.0.0.1');p.add_argument('--port',type=int,default=8786);p.add_argument('--data',type=Path,required=True);p.add_argument('--loopback-port',type=int,default=8788);a=p.parse_args()
    server=ThreadingHTTPServer((a.bind,a.port),Handler);server.store=Store(a.data)
    local=ThreadingHTTPServer(('127.0.0.1',a.loopback_port),Handler);local.store=server.store
    threading.Thread(target=local.serve_forever,daemon=True).start()
    print(f'Dream debugger listening on {a.bind}:{a.port}',flush=True);server.serve_forever()
