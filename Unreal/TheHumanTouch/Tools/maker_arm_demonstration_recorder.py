"""Opt-in single-table same-tick capture; no actor transforms or controller writes.

Caller supplies dedicated capture items, authoritative feedback verification and
an acknowledgement of the ACTUALLY APPLIED target. Call tick only after that
control/physics update; never feed planned future rows or cached review frames.
No automatic callback/start is installed by this module.
"""
import copy
import json
from pathlib import Path
import time
import unreal

ROLES=('overhead','wrist_l','wrist_r')

class Recorder:
    def __init__(self, directory, cameras, sample_provider, task, provenance):
        self.root=Path(directory)
        if self.root.exists():raise ValueError('Use a new recording directory')
        if set(cameras)!=set(ROLES):raise ValueError('Three dedicated cameras required')
        self.root.mkdir(parents=True);(self.root/'samples').mkdir()
        self.cameras=cameras;self.provider=sample_provider;self.index=0
        self.pending=None;self.origin=None;self.error=None;self.closed=False;self.finishing=False;self.latest_ack=None
        self.manifest={'task':task,'scope':'simulation','fps':5,'provenance':provenance,
                       'timing':'same game-thread tick capture requests; GPU readback completes asynchronously',
                       'review':{},'positive_eligible':False}
        self._manifest()

    def _manifest(self):
        p=self.root/'episode.tmp';p.write_text(json.dumps(self.manifest,indent=2));p.replace(self.root/'episode.json')

    def acknowledge(self, target_joints, action_unix):
        """Runtime calls only after both arms accept this actual target."""
        if self.closed:return
        if self.latest_ack and action_unix <= self.latest_ack['action_unix']:
            raise ValueError('Action acknowledgement time must increase')
        self.latest_ack={'applied_target_joints':copy.deepcopy(target_joints),
                         'action_unix':float(action_unix),'action_applied':True}

    def finish(self):
        """Drain the in-flight sample via tick(), without starting another."""
        self.finishing=True
        if self.pending is None:self.stop()

    def tick(self):
        if self.closed:return
        try:
            now=time.monotonic()
            if self.pending:
                packet,folder,issued=self.pending
                complete=True
                for role in ROLES:
                    p=folder/(role+'.jpg')
                    data=p.read_bytes() if p.exists() else b''
                    complete &= data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')
                if not complete:
                    if now-issued>.19:raise RuntimeError('GPU/export missed 5 Hz deadline; episode rejected')
                    return
                (folder/'sample.json').write_text(json.dumps(packet))
                folder.rename(self.root/'samples'/('%06d'%self.index))
                self.index+=1;self.pending=None
            if self.finishing:self.stop();return
            if self.origin is None:self.origin=now
            due=self.origin+self.index*.2
            if now<due:return
            if now-due>.025:raise RuntimeError('Missed sample deadline; no duplicated frames permitted')
            packet=copy.deepcopy(self.provider())
            # Provider must verify live actor state and acknowledge applied command.
            if packet.get('state_verified') is not True:
                raise RuntimeError('Unverified measured state')
            packet['latest_action_ack']=copy.deepcopy(self.latest_ack)
            packet.update(index=self.index,scope='simulation',camera_unix={},images={})
            folder=self.root/('.pending-%06d'%self.index);folder.mkdir()
            times=[packet['measured_unix']]
            for role in ROLES:
                item=self.cameras[role]
                # Dedicated captures must already follow current source transforms.
                # Calling capture_scene three times on the same tick avoids the
                # staggered observer's incompatible state/camera timestamp reuse.
                stamp=time.time();packet['camera_unix'][role]=stamp;times.append(stamp)
                item['capture'].capture_scene()
                options=unreal.ImageWriteOptions()
                options.set_editor_property('format',unreal.DesiredImageFormat.JPG)
                options.set_editor_property('compression_quality',95)
                options.set_editor_property('async_',True)
                options.set_editor_property('overwrite_file',False)
                unreal.ImageWriteBlueprintLibrary.export_to_disk(item['target'],str(folder/(role+'.jpg')),options)
                packet['images'][role]='samples/%06d/%s.jpg'%(self.index,role)
            if max(times)-min(times)>.02:raise RuntimeError('State/action/camera skew exceeds 20 ms')
            self.pending=(packet,folder,now)
        except Exception as error:
            self.error=str(error);self.stop()
            raise

    def stop(self):
        self.closed=True
        self.manifest.update(samples=self.index,error=self.error,incomplete_sample=self.pending is not None,
                             capture_complete=self.error is None and self.pending is None)
        self._manifest()
