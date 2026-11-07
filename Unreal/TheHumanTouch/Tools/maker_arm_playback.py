"""Bounded Dream joint trajectory preview in one shared editor transaction.
Kinematic playback only; does not simulate contact forces or command hardware.
"""
import json
import math
from pathlib import Path
import time
import unreal
import yam_station
import maker_arm_feedback
from maker_arm_actions import retime
from maker_arm_model import mounted_frames,ue_pose
from maker_arm_station import BASES,MOUNT_YAW,VISUAL_LINKS
from copresence import tracking,animation

_active=None

class Playback:
    def __init__(self,report,output,hz=20):
        if animation.is_running():raise RuntimeError('Another animation is active')
        self.output=Path(output);self.hz=hz
        report=json.loads(Path(report).read_text())
        self.chunks,self.timing=retime(report['action'],report['state'],hz)
        self.hz=self.timing['action_hz']
        existing=yam_station.actors()
        self.actors={(side,link):existing['MakerArm_'+side+'_'+link] for side in BASES for link in VISUAL_LINKS}
        # Reject a stale observation before opening a transaction or moving anything.
        for side in BASES:
            state=report['state'];q=state['q_'+side];gap=state['gap_'+side+'_m']
            for link,t in mounted_frames(q,gap,yaw=MOUNT_YAW[side]).items():
                if link not in VISUAL_LINKS:continue
                p,r=ue_pose(t);actor=self.actors[side,link];actual=actor.get_actor_transform()
                expected=[p[i]+BASES[side][i] for i in range(3)]
                position=actual.translation
                if max(abs(a-b) for a,b in zip((position.x,position.y,position.z),expected))>.01:raise ValueError('Observation pose is stale')
                rotation=actual.rotation
                if abs(sum(a*b for a,b in zip((rotation.x,rotation.y,rotation.z,rotation.w),r)))<.999999:raise ValueError('Observation orientation is stale')
            if max(abs(a-b) for a,b in zip(q,self.chunks[side][0][:6]))>.02:raise ValueError('Action starts too far from observed pose')
        maker_arm_feedback.seed(report['state'])
        self.before=tracking.snapshot();self.undo_before=tracking.undo_count()
        self.index=-1;self.started=time.monotonic();self.callback=None
        index=unreal.SystemLibrary.begin_transaction('CoPresence',unreal.Text(tracking.transaction_title('Dream Maker Arm trajectory')),next(iter(self.actors.values())))
        if index!=0:
            if index>0:unreal.SystemLibrary.end_transaction()
            raise RuntimeError('Start playback after enclosing transaction ends')
        try:
            for actor in self.actors.values():
                unreal.SystemLibrary.transact_object(actor);unreal.SystemLibrary.transact_object(actor.root_component)
            self.callback=unreal.register_slate_post_tick_callback(self.tick)
        except Exception:
            unreal.SystemLibrary.end_transaction();raise
        self.write(False)

    def write(self,done,error=None):
        self.output.parent.mkdir(parents=True,exist_ok=True)
        state={'done':done,'index':self.index,'rows':len(self.chunks['left']),'error':error,'scope':'editor kinematic playback','timing':self.timing}
        if self.index>=0:
            for side in BASES:
                row=self.chunks[side][self.index]
                state['q_'+side]=row[:6];state['gap_'+side+'_m']=row[6]*.104825
        temp=self.output.with_suffix('.tmp');temp.write_text(json.dumps(state));temp.replace(self.output)

    def tick(self,delta):
        try:
            if (self.output.parent/'stop').exists():self.stop('Stopped by user');return
            index=min(int((time.monotonic()-self.started)*self.hz),len(self.chunks['left'])-1)
            if index==self.index:return
            for side in BASES:
                row=self.chunks[side][index]
                transforms=mounted_frames(row[:6],row[6]*.104825,yaw=MOUNT_YAW[side])
                for link in VISUAL_LINKS:
                    actor=self.actors[side,link]
                    if not unreal.SystemLibrary.is_valid(actor):raise RuntimeError('Level changed during playback')
                    p,q=ue_pose(transforms[link]);p=[v+BASES[side][i] for i,v in enumerate(p)]
                    actor.set_actor_location_and_rotation(unreal.Vector(*p),unreal.Quat(*q).rotator(),False,True)
                    unreal.SystemLibrary.snapshot_object(actor.root_component)
            self.index=index
            state={}
            for side in BASES:
                row=self.chunks[side][index]
                state['q_'+side]=row[:6];state['gap_'+side+'_m']=row[6]*.104825
            maker_arm_feedback.applied(state)
            self.write(False)
            if index==len(self.chunks['left'])-1:self.stop()
        except Exception as error:self.stop(str(error))

    def stop(self,error=None):
        if self.callback is None:return
        unreal.unregister_slate_post_tick_callback(self.callback);self.callback=None
        unreal.SystemLibrary.end_transaction()
        tracking.current_task['transactions']+=tracking.undo_count()-self.undo_before
        self.write(True,error)

def start(report,output,hz=20):
    global _active
    if _active and _active.callback is not None:raise RuntimeError('Playback already active')
    _active=Playback(report,output,hz)

def stop():
    if _active:_active.stop('Stopped by user')


def launch(report,output,hz=20):
    try:start(report,output,hz)
    except Exception as error:
        Path(output).write_text(json.dumps({'done':True,'error':str(error)}))
