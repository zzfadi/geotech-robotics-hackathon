"""Local Unreal worker for Luna2's Dream debugger. Kinematic feedback preview."""
import argparse,base64,json,os,subprocess,sys,threading,time,urllib.request
from pathlib import Path
from dream import DreamClient
import imageio_ffmpeg
from model import prepare
from probe import configure

class Worker:
    def __init__(self,args):
        self.args=args;self.token=args.token_file.read_text().strip();self.command=None;self.closed=False;self.last=None
        self.root=args.data;self.root.mkdir(parents=True,exist_ok=True)
        self.recording=None;self.recorded=0
        self.lock=threading.Lock();self.client=DreamClient();self.prompt='';self.error=None
    def api(self,path,data=None):
        body=None if data is None else json.dumps(data,default=lambda x:x.tolist() if hasattr(x,'tolist') else str(x)).encode()
        req=urllib.request.Request(self.args.server+path,data=body,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
    def event(self,kind,**kwargs):
        try:self.api('/api/events',{'kind':kind,'request_id':(self.command or {}).get('id'),**kwargs})
        except Exception as error:print('Debugger event delivery failed: '+type(error).__name__,flush=True)
    def ue(self,tool,args):
        env=dict(os.environ,COPRESENCE_UNREAL_MCP_URL='http://127.0.0.1:8041/mcp')
        result=subprocess.run(['uv','run','--script','Tools/copresence_mcp.py','call',tool,json.dumps(args)],cwd=self.args.project,env=env,capture_output=True,text=True,timeout=45)
        if result.returncode or 'ERROR' in result.stdout or 'rejected' in result.stdout.lower():raise RuntimeError((result.stdout+result.stderr)[-1500:])
        return result.stdout
    def python(self,code,description):return self.ue('run_python',{'code':code,'description':description})
    def poll(self):
        while not self.closed:
            try:
                response=self.api('/api/worker/next');self.command=response['command']
                if self.command and self.command.get('stop'):(self.root/'stop').touch()
            except Exception:
                # Losing the operator connection stops motion and prevents further actions.
                (self.root/'stop').touch()
            time.sleep(1)
    def pump(self):
        sequence=None
        while not self.closed:
            try:
                packet=json.loads((self.root/'cameras/observation.json').read_text())
                if packet['sequence']!=sequence and time.time()-packet['captured_unix']<2:
                    sequence=packet['sequence'];self.last=packet
                    if self.recording:
                        self.recorded+=1
                        (self.recording/('frame_%06d.jpg'%self.recorded)).write_bytes(base64.b64decode(packet['frames']['video.overhead']))
                    if self.prompt:
                        obs={'prompt':self.prompt}
                        for side in ('left','right'):
                            obs['state.'+side+'_arm']=packet['state']['q_'+side]
                            obs['state.'+side+'_grip']=[packet['state']['gap_'+side+'_m']/.104825]
                        self.client.set_observation(packet['frames'],obs,timestamp=packet['captured_monotonic'])
            except FileNotFoundError:pass
            except Exception as error:self.error=str(error)
            time.sleep(.08)
    def fresh(self,after=0):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            if self.error:raise RuntimeError(self.error)
            if self.last and self.last['captured_unix']>after and time.time()-self.last['captured_unix']<2:return self.last
            if (self.root/'stop').exists():raise RuntimeError('Stopped by user')
            time.sleep(.1)
        raise TimeoutError('No fresh verified Unreal camera/state observation')
    def run_job(self,command):
        task=False;request_id=command['id']
        try:
            (self.root/'stop').unlink(missing_ok=True);self.error=None;self.last=None
            self.client.reset_episode();self.prompt=command['prompt']
            self.recording=self.root/'recordings'/request_id;self.recording.mkdir(parents=True,exist_ok=True);self.recorded=0
            self.ue('begin_task',{'title':'Dream web command','request':self.prompt,'keep_view':True,'focus_x':40,'focus_y':0,'focus_z':100});task=True
            seed=self.args.initial_state
            previous=self.root/'playback.json'
            if previous.exists() and 'q_left' in json.loads(previous.read_text()):seed=previous
            # Reset observers between episodes so stale images cannot enter a new command.
            (self.root/'cameras/observation.json').unlink(missing_ok=True)
            code=f"import importlib, json, maker_arm_feedback, maker_arm_observer, maker_arm_playback\nfrom copresence import ui_widgets\nmaker_arm_observer.stop()\nimportlib.reload(maker_arm_feedback)\nimportlib.reload(maker_arm_playback)\nimportlib.reload(maker_arm_observer)\nmaker_arm_feedback.seed(json.load(open({str(seed)!r})))\nui_widgets.after(1, lambda: maker_arm_observer.start({str(self.root/'cameras')!r}, seconds=120))"
            self.python(code,'Start verified three-camera feedback for Dream command')
            for cycle in range(self.args.cycles):
                packet=self.fresh();time.sleep(.3)
                if (self.root/'stop').exists():raise RuntimeError('Stopped by user')
                self.event('request.started',prompt=self.prompt,cycle=cycle+1,frames=packet['frames'],state=packet['state'])
                started=time.monotonic();action=self.client.get_action()
                if (self.root/'stop').exists():raise RuntimeError('Stopped by user; discarded pending model response')
                self.event('request.completed',cycle=cycle+1,elapsed_seconds=time.monotonic()-started,action={k:action[k] for k in ('q_l_chunk','q_r_chunk')})
                report=self.root/'action.json';report.write_text(json.dumps({'state':packet['state'],'action':action},default=lambda x:x.tolist() if hasattr(x,'tolist') else str(x)))
                previous.unlink(missing_ok=True)
                self.python(f"from copresence import ui_widgets\nimport maker_arm_playback\nui_widgets.after(.2, lambda: maker_arm_playback.launch({str(report)!r},{str(previous)!r}))",'Apply bounded Dream response in one shared transaction')
                self.event('playback.started',cycle=cycle+1,message='Applying Dream joint targets in Unreal')
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    if previous.exists():
                        state=json.loads(previous.read_text())
                        if state.get('done'):
                            if state.get('error'):raise RuntimeError(state['error'])
                            break
                    time.sleep(.1)
                else:raise TimeoutError('Unreal playback did not finish')
                self.event('playback.completed',cycle=cycle+1,state=state,message='48-step kinematic trajectory finished')
                packet=self.fresh(after=time.time());self.event('observation',frames=packet['frames'],state=packet['state'])
            self.api('/api/worker/done',{'request_id':request_id,'status':'done'})
        except Exception as error:
            self.event('request.failed',message=str(error));self.api('/api/worker/done',{'request_id':request_id,'status':'stopped' if (self.root/'stop').exists() else 'failed'})
        finally:
            self.prompt=''
            recording=self.recording;self.recording=None
            if recording and self.recorded:
                subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-framerate','5','-i',str(recording/'frame_%06d.jpg'),'-c:v','libx264','-pix_fmt','yuv420p',str(recording/'unreal-motion.mp4')],capture_output=True,timeout=20)
            if task:
                try:
                    self.python('import maker_arm_observer; maker_arm_observer.stop()','Stop local camera capture')
                    self.ue('end_task',{'summary':'Dream web command finished; see debugger for actual request and kinematic playback result. Contact/grasp success is not established.','keep_view':True})
                except Exception as error:self.event('cleanup.failed',message=str(error))
    def run(self):
        configure(self.client,prepare(self.root/'model'))
        if not self.client.wait_for_ik(180):raise TimeoutError('IK initialization timed out')
        threading.Thread(target=self.poll,daemon=True).start();threading.Thread(target=self.pump,daemon=True).start()
        self.event('worker.ready',message='Unreal worker ready for text commands')
        seen=set()
        try:
            while True:
                command=self.command
                if command and command['status']=='running' and command['id'] not in seen:
                    seen.add(command['id']);self.run_job(command)
                time.sleep(.2)
        finally:self.closed=True;self.client.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--server',required=True);p.add_argument('--token-file',type=Path,required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--initial-state',type=Path,required=True);p.add_argument('--cycles',type=int,default=3);args=p.parse_args();Worker(args).run()
