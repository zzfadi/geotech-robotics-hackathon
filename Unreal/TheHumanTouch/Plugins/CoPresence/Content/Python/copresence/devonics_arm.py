"""Segmented six-axis visual arm, sampled from a joint-limited offline IK solution."""
import json,os,bisect,math
import unreal
NAMES=['DV_Base','DV_Shoulder','RS_UpperArm','RS_Forearm','DV_Wrist1','DV_Wrist2','DV_Wrist3']
class Arm:
 def __init__(self,controller):
  self.controller=controller
  self.actors=[controller.actors[n] for n in NAMES]
  path=os.path.join(unreal.Paths.project_dir(),'Art','DevonicsArm','motion.json')
  self.frames=json.load(open(path))['frames'];self.times=[f['t'] for f in self.frames]
  controller.owned.extend(self.actors)
 def pose(self,t):
  i=max(0,min(len(self.frames)-2,bisect.bisect_right(self.times,t)-1));a,b=self.frames[i:i+2];u=max(0,min(1,(t-a['t'])/(b['t']-a['t'])))
  for actor,x,y in zip(self.actors,a['links'],b['links']):
   p=[v+(w-v)*u for v,w in zip(x['p'],y['p'])];qa,qb=x['q'],y['q'];sign=1 if sum(v*w for v,w in zip(qa,qb))>=0 else -1;q=[v+(sign*w-v)*u for v,w in zip(qa,qb)];length=math.sqrt(sum(v*v for v in q));q=[v/length for v in q]
   actor.set_actor_location_and_rotation(unreal.Vector(*self.controller.w(p)),unreal.Quat(*q).rotator(),False,False);unreal.SystemLibrary.snapshot_object(actor.root_component)
