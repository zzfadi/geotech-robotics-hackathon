"""Local Unreal worker for Luna2's Dream debugger. Kinematic feedback preview."""
import argparse,base64,json,os,subprocess,sys,threading,time,urllib.request
from pathlib import Path
from dream import DreamClient
from dream.errors import DreamSessionExpired, DreamServerError
from model_routing import ModelBudget, FLASH, FULL, red_cube_lift
import imageio_ffmpeg
from model import prepare
from probe import configure
from queueing import wait_for_editor
from state_seed import recover_state
from session_recovery import request_action

class Worker:
    def __init__(self,args):
        self.args=args;self.token=args.token_file.read_text().strip();self.command=None;self.voice=None;self.asr=None;self.closed=False;self.last=None
        self.root=args.data;self.root.mkdir(parents=True,exist_ok=True)
        self.recording=None;self.recorded=0;self.expected_scope=None;self.active_request_id=None;self.episode_started=0
        self.lock=threading.Lock();self.client=DreamClient();self.prompt='';self.error=None
        self.checkpoint=None;self.arm=None
    def api(self,path,data=None):
        body=None if data is None else json.dumps(data,default=lambda x:x.tolist() if hasattr(x,'tolist') else str(x)).encode()
        req=urllib.request.Request(self.args.server+path,data=body,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
    def event(self,kind,**kwargs):
        try:self.api('/api/events',{'kind':kind,'request_id':self.active_request_id or (self.command or {}).get('id'),**kwargs})
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
                response=self.api('/api/worker/next');self.command=response['command'];self.voice=response.get('voice')
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
                    sequence=packet['sequence']
                    with self.lock:
                        if self.recording:
                            self.recorded+=1
                            for role in ('overhead','wrist_l','wrist_r'):
                                prefix='frame' if role=='overhead' else role
                                (self.recording/(prefix+'_%06d.jpg'%self.recorded)).write_bytes(base64.b64decode(packet['frames']['video.'+role]))
                        if self.prompt and packet['captured_unix']>=self.episode_started and (self.expected_scope is None or packet.get('scope')==self.expected_scope):
                            obs={'prompt':self.prompt}
                            for side in ('left','right'):
                                obs['state.'+side+'_arm']=packet['state']['q_'+side]
                                obs['state.'+side+'_grip']=[packet['state']['gap_'+side+'_m']/.104825]
                            self.client.set_observation(packet['frames'],obs,timestamp=packet['captured_monotonic'])
                        # Publish only after the observation is installed in Dream's window.
                        self.last=packet
            except FileNotFoundError:pass
            except Exception as error:self.error=str(error)
            time.sleep(.08)
    def stream(self):
        sent=None
        while not self.closed:
            packet=self.last
            command=self.command
            request_id=self.active_request_id or (command or {}).get('id')
            if packet and time.time()-packet['captured_unix']<2:
                identity=(request_id,packet['captured_unix'])
                if identity!=sent:
                    try:
                        self.api('/api/worker/frame',{'request_id':request_id,'recording':bool(self.recording),'sequence':packet['sequence'],'captured_unix':packet['captured_unix'],'frames':packet['frames']})
                        sent=identity
                    except Exception:pass
            time.sleep(.5)

    def fresh(self,after=0,scope=None):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            if self.error:raise RuntimeError(self.error)
            packet=self.last
            if packet and packet['captured_unix']>max(after,self.episode_started) and time.time()-packet['captured_unix']<2 and (scope is None or packet.get('scope')==scope):return packet
            if (self.root/'stop').exists():raise RuntimeError('Stopped by user')
            time.sleep(.1)
        raise TimeoutError('No fresh verified Unreal camera/state observation')
    def select_model(self,checkpoint):
        if self.checkpoint == checkpoint:return
        with self.lock:
            configure(self.client,self.arm,checkpoint)
            if not self.client.wait_for_ik(30):raise TimeoutError('Model switch IK initialization timed out')
            self.checkpoint=checkpoint
            # policy() clears the observation window. Require a new installed packet.
            self.last=None;self.episode_started=time.time()
        self.event('model.selected',checkpoint=checkpoint,message='Using '+checkpoint)

    def infer(self,budget,cycle,scope):
        replacement_packet=None
        def call():
            nonlocal replacement_packet
            if self.args.checkpoint == 'auto':
                # Session renewal can retry once: count that as another actual call.
                if self.checkpoint == FULL and budget.full_calls >= budget.full_limit:
                    self.select_model(FLASH)
                    replacement_packet=self.fresh(after=time.time(),scope=scope)
                budget.charge(self.checkpoint)
            self.event('model.call',checkpoint=self.checkpoint,cycle=cycle+1,
                       full_calls=budget.full_calls,message='Calling '+self.checkpoint)
            return self.client.get_action()
        def request():
            action,packet=request_action(call,lambda:self.fresh(after=time.time(),scope=scope),
                lambda:(self.root/'stop').exists(),
                lambda message:self.event('session.recovering',cycle=cycle+1,message=message),DreamSessionExpired)
            return action,replacement_packet or packet
        try:return request()
        except DreamServerError as error:
            if self.args.checkpoint != 'auto' or not budget.capacity_fallback(self.checkpoint,error):raise
            self.event('model.fallback',checkpoint=FLASH,cycle=cycle+1,
                       message='Full model unavailable ('+str(error.reason)+'); using Flash for the rest of this command')
            self.select_model(FLASH)
            return request()

    def run_job(self,command):
        task=False;runtime_started=False;request_id=command['id'];result='failed'
        self.active_request_id=request_id
        budget=ModelBudget()
        try:
            self.select_model(FLASH if self.args.checkpoint == 'auto' else self.args.checkpoint)
            (self.root/'stop').unlink(missing_ok=True);self.error=None;self.last=None
            with self.lock:
                self.expected_scope='runtime' if self.args.runtime_physics else None
                self.client.reset_episode();self.prompt=command['prompt'];self.episode_started=time.time()
                self.recording=self.root/'recordings'/request_id;self.recording.mkdir(parents=True,exist_ok=True);self.recorded=0
            wait_for_editor(lambda:self.ue('begin_task',{'title':'Dream web command','request':self.prompt,'keep_view':True,'focus_x':40,'focus_y':0,'focus_z':100}),lambda:(self.root/'stop').exists(),lambda message:self.event('command.waiting',message=message));task=True
            previous=self.root/'playback.json'
            seed=self.root/'seed.json'
            seed.write_text(json.dumps(recover_state(previous,self.root/'cameras/observation.json',self.args.initial_state)))
            # Reset observers between episodes so stale images cannot enter a new command.
            (self.root/'cameras/observation.json').unlink(missing_ok=True)
            code=f"import importlib, json, maker_arm_feedback, maker_arm_observer, maker_arm_playback\nfrom copresence import ui_widgets\nmaker_arm_observer.stop()\nimportlib.reload(maker_arm_feedback)\nimportlib.reload(maker_arm_playback)\nimportlib.reload(maker_arm_observer)\nmaker_arm_feedback.seed(json.load(open({str(seed)!r})))\nui_widgets.after(1, lambda: maker_arm_observer.start({str(self.root/'cameras')!r}, seconds=86400))"
            if self.args.runtime_physics:
                runtime_status=self.root/'runtime.json'
                runtime_status.unlink(missing_ok=True)
                runtime_started=True
                code=f"import maker_arm_runtime\nfrom copresence import ui_widgets\nui_widgets.after(.2,lambda:maker_arm_runtime.start({str(seed)!r},{str(runtime_status)!r},{str(self.root/'cameras')!r},mirror=True,pad_contacts={self.args.pad_contacts!r}))"
                self.python(code,'Start authoritative runtime physics and camera feedback')
                self.wait_runtime(runtime_status,'ready')
            else:
                self.python(code,'Start verified three-camera feedback for Dream command')
            scope='runtime' if self.args.runtime_physics else None
            for cycle in range(self.args.cycles):
                if self.args.checkpoint == 'auto':
                    lift=None
                    if self.args.runtime_physics:
                        lift=red_cube_lift(json.loads((self.root/'runtime.json').read_text()))
                    self.select_model(budget.choose(cycle,lift))
                packet=self.fresh(scope=scope);time.sleep(.3)
                if (self.root/'stop').exists():raise RuntimeError('Stopped by user')
                assisted=self.args.grasp_assist and cycle>0
                source='local grasp assistance' if assisted else self.checkpoint
                if assisted:
                    from grasp_assist import action as assisted_action,PHASES
                    if not self.args.runtime_physics or self.args.tcp_back!=0.:raise ValueError('Grasp assistance requires runtime physics and the source TCP (--tcp-back 0)')
                    action=assisted_action(cycle-1,packet['state'],json.loads((self.root/'runtime.json').read_text()))
                    self.event('controller.assisted',cycle=cycle+1,message='Local grasp assistance: '+PHASES[cycle-1]+'. Uses measured simulation prop pose; not raw Dream policy output.')
                else:
                    self.event('request.started',checkpoint=self.checkpoint,prompt=self.prompt,cycle=cycle+1,frames=packet['frames'],state=packet['state'])
                    started=time.monotonic()
                    action,packet=self.infer(budget,cycle,scope)
                    self.event('request.completed',checkpoint=self.checkpoint,cycle=cycle+1,elapsed_seconds=time.monotonic()-started,action={k:action[k] for k in ('q_l_chunk','q_r_chunk')})
                raw_action=action;ik_report=None
                if self.args.position_ik:
                    from position_ik import adapt
                    action,ik_report=adapt(raw_action,packet['state'],self.args.action_rows or 12)
                    source='Dream EEF targets with position-priority IK'
                    self.event('controller.ik',cycle=cycle+1,message='Following Dream tool positions and jaw commands; position-priority IK, no object-pose input or scripted lift.',action={k:action[k] for k in ('q_l_chunk','q_r_chunk')})
                if (self.root/'stop').exists():raise RuntimeError('Stopped by user; discarded pending controller response')
                report=self.root/'action.json';report.write_text(json.dumps({'controller_source':source,'contact_mode':'approximate flat pads' if self.args.pad_contacts else 'approximate convex jaws','checkpoint':None if assisted else self.checkpoint,'state':packet['state'],'raw_action':raw_action if self.args.position_ik else None,'ik_report':ik_report,'action':action,'execution_rows':None if assisted else self.args.action_rows},default=lambda x:x.tolist() if hasattr(x,'tolist') else str(x)))
                # Preserve raw EEF targets beside solved joints for calibration
                # diagnosis; the controller still reads the current action file.
                (self.recording/f'action-{cycle+1}.json').write_text(report.read_text())
                previous.unlink(missing_ok=True)
                controller='maker_arm_runtime' if self.args.runtime_physics else 'maker_arm_playback'
                self.python(f"from copresence import ui_widgets\nimport {controller}\nui_widgets.after(.2, lambda: {controller}.launch({str(report)!r},{str(previous)!r}))",'Apply bounded '+source+' trajectory to the authoritative controller')
                self.event('playback.queued',cycle=cycle+1,message='Validating Dream trajectory before movement')
                playback_announced=False
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if previous.exists():
                        state=json.loads(previous.read_text())
                        if not state.get('error') and not playback_announced:
                            self.event('playback.started',cycle=cycle+1,message='Unreal accepted trajectory; motion started')
                            playback_announced=True
                        if state.get('done'):
                            if state.get('error'):raise RuntimeError(state['error'])
                            break
                    time.sleep(.1)
                else:raise TimeoutError('Unreal playback did not finish')
                mode='runtime physics' if self.args.runtime_physics else 'kinematic'
                self.event('playback.completed',cycle=cycle+1,state=state,message=str(state['rows'])+'-sample '+mode+' trajectory finished')
                packet=self.fresh(after=time.time(),scope=scope);self.event('observation',frames=packet['frames'],state=packet['state'])
                if self.args.verify_red_lift and self.args.runtime_physics:
                    red=json.loads((self.root/'runtime.json').read_text()).get('props',{}).get('MakerStation_Approximate_Block_Red',{})
                    if red.get('height_above_start_cm',0)>=10 and red.get('speed_cm_s',100)<10:
                        self.event('task.holding',message='Measured red-block rise reached 10 cm; holding the current robot pose for verification')
                        time.sleep(2.2)
                        if (self.root/'stop').exists():raise RuntimeError('Stopped by user during hold verification')
                        red=json.loads((self.root/'runtime.json').read_text()).get('props',{}).get('MakerStation_Approximate_Block_Red',{})
                        if red.get('current_slow_above_10cm_seconds',0)>=2:
                            self.event('task.measured',message=f"Measured {red['height_above_start_cm']:.2f} cm lift held for {red['current_slow_above_10cm_seconds']:.2f} seconds; visual grasp review still required")
                            break
            if runtime_started:
                self.finish_runtime(True)
                runtime_started=False
            result='done'
        except Exception as error:
            stopped=(self.root/'stop').exists()
            (self.root/'stop').touch()
            result='stopped' if stopped else 'failed'
            self.event('request.failed',message=str(error))
        finally:
            if runtime_started:
                try:
                    self.finish_runtime(False)
                except Exception as error:
                    result='failed';self.event('cleanup.failed',message='Runtime cleanup: '+str(error))
                finally:
                    # Aborted runtime output can never seed the restored editor.
                    (self.root/'playback.json').unlink(missing_ok=True)
            with self.lock:
                self.prompt='';self.expected_scope=None
                recording=self.recording;self.recording=None
            if recording and self.recorded:
                for role in ('overhead','wrist_l','wrist_r'):
                    try:
                        prefix='frame' if role=='overhead' else role
                        video=recording/('unreal-motion.mp4' if role=='overhead' else role+'.mp4')
                        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-loglevel','error','-framerate','5','-i',str(recording/(prefix+'_%06d.jpg')),'-c:v','libx264','-pix_fmt','yuv420p',str(video)],capture_output=True,timeout=20,check=True)
                        if video.stat().st_size>=1_400_000:raise ValueError('Recording exceeds upload limit')
                        self.api('/api/worker/video',{'request_id':request_id,'prompt':command['prompt'],'frames':self.recorded,'result':result,'role':role,'video':base64.b64encode(video.read_bytes()).decode()})
                    except Exception as error:self.event('recording.failed',message=role+': '+str(error))
            if task:
                try:
                    self.ue('end_task',{'summary':'Dream web command finished; see debugger for request and controller result. Task success requires measured object verification.','keep_view':True})
                except Exception as error:
                    result='failed';self.event('cleanup.failed',message=str(error))
            # Keep the queue occupied until physics, recordings and shared task
            # cleanup finish; a new operator command must not race teardown.
            try:self.api('/api/worker/done',{'request_id':request_id,'status':result})
            finally:self.active_request_id=None
    def wait_runtime(self,path,phase):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            if path.exists():
                state=json.loads(path.read_text())
                if state.get('phase')==phase:return state
                if state.get('phase') in ('error','stopped') and state.get('error'):raise RuntimeError(state['error'])
            time.sleep(.1)
        raise TimeoutError('Unreal runtime did not reach '+phase)
    def finish_runtime(self,commit):
        self.python(f"from copresence import ui_widgets\nimport maker_arm_runtime\nui_widgets.after(.2,lambda:maker_arm_runtime.stop(commit={commit!r}))",'Finish runtime physics and restore continuous editor preview')
        state=self.wait_runtime(self.root/'runtime.json','stopped')
        self.event('runtime.finished',state=state,message='Runtime physics ended; task outcome requires measured verification')
        if commit and state.get('error'):raise RuntimeError(state['error'])
    def transcribe(self,voice):
        import io
        try:
            from faster_whisper import WhisperModel
            if self.asr is None:self.asr=WhisperModel('base.en',device='cpu',compute_type='int8')
            segments,info=self.asr.transcribe(io.BytesIO(base64.b64decode(voice['audio'])),language='en',beam_size=5,vad_filter=True)
            if info.duration>30:raise ValueError('Voice command exceeds 30 seconds')
            text=' '.join(segment.text.strip() for segment in segments).strip()
            if not text:raise ValueError('No speech detected')
            self.api('/api/worker/transcript',{'id':voice['id'],'text':text})
        except Exception as error:self.api('/api/worker/transcript',{'id':voice['id'],'error':str(error)})
    def start_preview(self):
        seed=self.root/'preview-seed.json'
        seed.write_text(json.dumps(recover_state(self.root/'playback.json',self.root/'cameras/observation.json',self.args.initial_state)))
        wait_for_editor(lambda:self.ue('begin_task',{'title':'Start live camera preview','request':'Stream the three virtual cameras to the web app without moving the arms.','keep_view':True,'focus_x':40,'focus_y':0,'focus_z':100}),lambda:False,lambda message:self.event('preview.waiting',message=message))
        try:
            self.python(f"import json,maker_arm_feedback,maker_arm_observer\nfrom copresence import ui_widgets\nmaker_arm_observer.stop()\nmaker_arm_feedback.seed(json.load(open({str(seed)!r})))\nui_widgets.after(1,lambda:maker_arm_observer.start({str(self.root/'cameras')!r},seconds=86400))",'Start continuous local camera preview')
        finally:self.ue('end_task',{'summary':'Started local three-camera preview; robot poses unchanged.','keep_view':True})

    def run(self):
        self.arm=prepare(self.root/'model',tcp_back=self.args.tcp_back)
        configure(self.client,self.arm,FLASH if self.args.checkpoint == 'auto' else self.args.checkpoint)
        self.checkpoint=FLASH if self.args.checkpoint == 'auto' else self.args.checkpoint
        if not self.client.wait_for_ik(180):raise TimeoutError('IK initialization timed out')
        threading.Thread(target=self.poll,daemon=True).start();threading.Thread(target=self.pump,daemon=True).start();threading.Thread(target=self.stream,daemon=True).start()
        self.start_preview()
        self.event('worker.ready',message='Unreal worker ready'+(' — local grasp assistance enabled for side-grasp red-block fixture' if self.args.grasp_assist else ' for text commands'))
        seen=set();heard=set()
        try:
            while True:
                voice=self.voice
                if voice and voice['id'] not in heard:
                    heard.add(voice['id']);self.transcribe(voice)
                command=self.command
                if command and command['status']=='running' and command['id'] not in seen:
                    seen.add(command['id']);self.run_job(command)
                time.sleep(.2)
        finally:self.closed=True;self.client.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--server',required=True);p.add_argument('--token-file',type=Path,required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--data',type=Path,required=True);p.add_argument('--initial-state',type=Path,required=True);p.add_argument('--cycles',type=int,default=3);p.add_argument('--action-rows',type=int,default=None);p.add_argument('--runtime-physics',action='store_true');p.add_argument('--position-ik',action='store_true',help='Follow raw Dream EEF positions with position-priority IK');p.add_argument('--verify-red-lift',action='store_true',help='Hold and finish after a measured 10 cm red-block lift');p.add_argument('--pad-contacts',action='store_true',help='Approximate flat collision pads; official visual meshes unchanged');p.add_argument('--grasp-assist',action='store_true',help='Explicit local physics grasp servo after one Dream approach; use --cycles 5 --tcp-back 0');p.add_argument('--tcp-back',type=float,default=0.,help='Simulation-only contact-center offset behind source TCP, metres (0..0.04)');p.add_argument('--checkpoint',default='auto',help='auto: Flash first, at most two full-model calls on a stalled lift; or an explicit checkpoint');args=p.parse_args()
    if args.grasp_assist and (args.cycles!=5 or not args.runtime_physics or args.tcp_back!=0):p.error('Grasp assistance requires --cycles 5 --runtime-physics --tcp-back 0')
    if args.position_ik and (args.grasp_assist or args.tcp_back!=0):p.error('Position IK requires source TCP and excludes scripted grasp assistance')
    Worker(args).run()
