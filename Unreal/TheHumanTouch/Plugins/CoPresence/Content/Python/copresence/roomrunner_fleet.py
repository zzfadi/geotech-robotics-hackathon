"""Three charging robots sharing one packing arm and elevator, one order at a time."""
import math,time,os,json
import unreal
from collections import deque
from copresence.roomrunner_full_delivery import FullDelivery
from copresence.roomrunner_sim import xyz,ROOT
HOMES=[(-3100,2220,0),(-2920,2220,0),(-2740,2220,0)]
LABELS=['RoomRunner_DeliveryRobot','RoomRunner_Fleet_02','RoomRunner_Fleet_03']
PRODUCTS={'snack':'Snack box','drink':'Bottled drink','toiletries':'Toiletry kit'}
class FleetDelivery(FullDelivery):
 def __init__(self):
  super().__init__()
  self.fleet=[];self.selected=0;self.order_items=['snack'];self.next_items=['snack'];self.follow_camera=False
  for i,label in enumerate(LABELS):
   robot=self.actors[label];rig=dict(self.rig)
   if i:
    for key in ['RS_DoorL','RS_DoorR','RS_LoadingTote','RS_PickSnack']:rig[key]=self.actors[f'Fleet_{i+1:02d}_{key}']
   row={'robot':robot,'rig':rig,'home':HOMES[i],'battery':100.0,'last_used':0,'status':'CHARGING'};self.fleet.append(row)
   self.owned += [robot]+list(rig.values())+[self.actors[f'Fleet_{i+1:02d}_Extra_{j}'] for j in [1,2]]
  self.owned=list(dict.fromkeys(self.owned));self.event('Fleet ready: three charging bays, automatic robot selection')
 def fleet_status(self):
  return [{'id':i+1,'label':r['robot'].get_actor_label(),'battery':round(r['battery'],1),'status':r['status'],'home':r['home']} for i,r in enumerate(getattr(self,'fleet',[]))]
 def command(self,c):
  if c.get('action')=='start':
   items=c.get('items',['snack'])
   if not isinstance(items,list) or not items or any(i not in PRODUCTS for i in items):raise ValueError('Select one or more available items')
   self.next_items=list(dict.fromkeys(items))
  super().command(c)
 def start(self,floor,room):
  if self.state not in ['IDLE','COMPLETE','CANCELLED']:raise ValueError('One delivery is already active')
  if not 1<=floor<=10 or not 1<=room<=8:raise ValueError('Invalid room')
  candidates=[(r['last_used'],i) for i,r in enumerate(self.fleet) if r['battery']>=30 and math.dist(xyz(r['robot']),r['home'])<8]
  if not candidates:raise ValueError('No charged robot at a bay; wait for charging or return the active robot')
  self.selected=min(candidates)[1];row=self.fleet[self.selected];self.robot=row['robot'];self.rig=row['rig'];self.cargo=[self.rig[n] for n in ['RS_DoorL','RS_DoorR','RS_LoadingTote','RS_PickSnack']]
  self.order_items=list(self.next_items);row['last_used']=time.time();row['status']='DELIVERING'
  self.camera_position=None;self.return_home_added=False
  super().start(floor,room)
  # Pull out of the charging lane before crossing toward the packing arm.
  self.queue.insert(1,self.nav([row['home'][0],2100,0]))
  meshes={'snack':'SM_RS_Snack','drink':'SM_RS_Bottle','toiletries':'SM_RS_Snack'}
  self.parcel_actors=[self.rig['RS_PickSnack']]+[self.actors[f'Fleet_{self.selected+1:02d}_Extra_{j}'] for j in [1,2]]
  for j,actor in enumerate(self.parcel_actors):
   visible=j<len(self.order_items)
   actor.set_is_temporarily_hidden_in_editor(not visible);actor.set_actor_hidden_in_game(not visible)
   if visible:
    product=self.order_items[j];actor.get_component_by_class(unreal.StaticMeshComponent).set_static_mesh(unreal.load_asset('/Game/RoomRunnerStation/Meshes/'+meshes[product]))
    actor.set_actor_scale3d(unreal.Vector(1,1,1) if product!='toiletries' else unreal.Vector(.8,1,.7))
   unreal.SystemLibrary.snapshot_object(actor)
  self.event('Robot %02d unplugged: %s'%(self.selected+1,', '.join(PRODUCTS[x] for x in self.order_items)))
 def update(self,dt):
  if not hasattr(self,'fleet'):return super().update(dt)
  super().update(dt)
  if self.active and self.active['kind']=='dock' and not self.paused:
   self.state='DOCKING';self.frame_dt=dt
   self.move(self.robot,xyz(self.robot),180)
   self.follow_cargo()
   if abs((self.robot.get_actor_rotation().yaw-180+180)%360-180)<.5:
    self.event('Robot %02d docked facing the room; charging resumed'%(self.selected+1))
    self.finish_action()
  if self.returning and not getattr(self,'return_home_added',False):
   home=self.fleet[self.selected]['home'];self.queue.extend([self.nav([home[0],2100,0]),self.nav(list(home)),{'kind':'dock'}]);self.return_home_added=True
   self.fleet[self.selected]['status']='RETURNING TO CHARGER'
   self.event('Returning robot %02d to its charging bay'%(self.selected+1))
  if getattr(self,'parcel_actors',None) and self.state not in ['IDLE','COMPLETE','CANCELLED','FAULT']:
   base=xyz(self.parcel_actors[0]);yaw=math.radians(self.robot.get_actor_rotation().yaw-90)
   for j,actor in enumerate(self.parcel_actors[1:],1):
    if j<len(self.order_items):
     offset=11 if j==1 else -11
     self.move(actor,(base[0]+offset*math.cos(yaw),base[1]+offset*math.sin(yaw),base[2]))
  active=self.state not in ['IDLE','COMPLETE','CANCELLED','FAULT']
  for i,row in enumerate(self.fleet):
   at_home=math.dist(xyz(row['robot']),row['home'])<8
   if active and i==self.selected:
    if not self.paused:row['battery']=max(0,row['battery']-.025*dt)
   elif at_home:
    if not self.paused:row['battery']=min(100,row['battery']+.25*dt)
    row['status']='READY · CHARGING' if row['battery']>=30 else 'CHARGING'
   else:row['status']='AWAY FROM CHARGER'
  if not active:self.follow_camera=False
