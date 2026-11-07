"""Live editor delivery simulation. Uses collision queries, A* and a lift state machine.
No Sequencer or prescribed movement timing. Only the Codex editor runs the controller.
"""
import json, math, os, time, traceback
from collections import deque
import unreal
from copresence.delivery_planner import plan
ROOT=os.path.join(unreal.Paths.project_dir(),'Saved','RoomRunnerSimulation')
os.makedirs(ROOT,exist_ok=True)
_instance=None

def vec(p):return unreal.Vector(*p)
def xyz(a):return tuple(a.get_actor_location().to_tuple())
def floor_of(a):return max(1,min(10,round(xyz(a)[2]/400)+1))
class Delivery:
 def __init__(self):
  self.es=unreal.get_editor_subsystem(unreal.EditorActorSubsystem);self.world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
  self.actors={a.get_actor_label():a for a in self.es.get_all_level_actors()};self.robot=self.actors['RoomRunner_DeliveryRobot'];self.car=self.actors['Hotel_LiftCar']
  self.doors={(f,s):self.actors[f'Hotel_LiftDoor_{f}_{s}'] for f in range(1,11) for s in ['L','R']}
  self.people=[a for n,a in self.actors.items() if n.startswith('Hotel_Pedestrian_')];self.people_goals={}
  self.owned=[self.robot,self.car]+list(self.doors.values())+self.people;self.queue=deque();self.path=[];self.active=None;self.state='IDLE';self.paused=False;self.transaction=False;self.target=None;self.payload=False;self.replans=0;self.avoidance_stops=0;self.distance=0;self.error=None;self.events=[];self.lastwrite=0;self.lastcommand=None;self.retry=0;self.wait=0;self.returning=False
  self.lastcommand=json.load(open(ROOT+'/command.json')).get('id') if os.path.exists(ROOT+'/command.json') else None
  self.callback=unreal.register_slate_post_tick_callback(self.tick);self.event('Controller ready')
 def event(self,message):
  self.events.append({'time':time.time(),'message':message});self.events=self.events[-60:];unreal.log('RoomRunner: '+message);self.write()
 def write(self):
  data={'robot_label':self.robot.get_actor_label(),'fleet':self.fleet_status() if hasattr(self,'fleet_status') else [],'order_items':getattr(self,'order_items',['snack']),'follow_camera':getattr(self,'follow_camera',True),'returning':self.returning,'state':self.state,'paused':self.paused,'target':self.target,'payload_loaded':self.payload,'robot_position':xyz(self.robot),'robot_floor':floor_of(self.robot),'elevator_floor':round(xyz(self.car)[2]/400+1,2),'distance_cm':round(self.distance),'replans':self.replans,'avoidance_stops':self.avoidance_stops,'planned_path':self.path,'error':self.error,'current_action':self.active,'queued_actions':len(self.queue),'events':self.events,'updated':time.time()}
  tmp=ROOT+'/status.tmp';open(tmp,'w').write(json.dumps(data,indent=2));os.replace(tmp,ROOT+'/status.json')
 def begin(self):
  unreal.SystemLibrary.begin_transaction('CoPresence',unreal.Text('Codex: autonomous RoomRunner delivery'),self.robot)
  for a in self.owned:
   unreal.SystemLibrary.transact_object(a);unreal.SystemLibrary.transact_object(a.root_component)
  self.transaction=True
 def end(self):
  if self.transaction:unreal.SystemLibrary.end_transaction();self.transaction=False
 def move(self,a,p,yaw=None):
  if a==self.robot and yaw is not None and self.state not in ['STATION_PICK_PACK_LOAD','LOADING']:
   current=a.get_actor_rotation().yaw;delta=(yaw-current+180)%360-180
   limit=120*getattr(self,'frame_dt',1/60)
   yaw=current+max(-limit,min(limit,delta))
  r=a.get_actor_rotation() if yaw is None else unreal.Rotator(pitch=0,yaw=yaw,roll=0)
  a.set_actor_location_and_rotation(vec(p),r,False,False);unreal.SystemLibrary.snapshot_object(a.root_component)
 def clear(self,a,b,ignore_extra=()):
  ignore=[self.robot]+list(ignore_extra)
  hit=unreal.SystemLibrary.capsule_trace_single(self.world,vec((a[0],a[1],a[2]+65)),vec((b[0],b[1],b[2]+65)),34,60,unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,True,ignore,unreal.DrawDebugTrace.NONE,True)
  return hit is None
 def walkable(self,p,z):
  x,y=p
  return ((-965<=x<=1365 and 935<=y<=2565) or (z<100 and -3165<=x<=-965 and 1335<=y<=2265) or (1360<=x<=1600 and 1635<=y<=1865))
 def nav(self,p):return {'kind':'navigate','goal':p}
 def door(self,f,opened):return {'kind':'door','floor':f,'open':opened}
 def lift(self,source,dest):
  if source==dest:return []
  z=(source-1)*400;dz=(dest-1)*400
  return [self.nav([1220,1750,z])]+[self.door(f,False) for f in range(1,11)]+[{'kind':'lift','z':z,'occupied':False},self.door(source,True),self.nav([1520,1750,z]),self.door(source,False),{'kind':'lift','z':dz,'occupied':True},self.door(dest,True),self.nav([1220,1750,dz]),self.door(dest,False)]
 def start(self,floor,room):
  if self.state not in ['IDLE','COMPLETE','CANCELLED']:raise ValueError('Delivery already active; cancel or complete it first')
  if not 1<=floor<=10 or not 1<=room<=8:raise ValueError('Floor must be 1..10 and room 1..8')
  # Finish the idle crowd transaction before opening the delivery transaction.
  self.end()
  self.target={'floor':floor,'room':floor*100+room};self.error=None;self.returning=False;self.paused=False;self.distance=0;self.replans=0;self.avoidance_stops=0;self.payload=False;self.path=[];self.active=None
  current=floor_of(self.robot);stages=self.lift(current,1)+[self.nav([-2350,2100,0]),{'kind':'load'}]+self.lift(1,floor)
  x=[-700,-100,500,1100][(room-1)%4];y=1520 if room<=4 else 1980;z=(floor-1)*400
  stages += [self.nav([x,1750,z]),self.nav([x,y,z]),{'kind':'guest'}]
  self.queue=deque(stages);self.state='STARTING';self.begin();self.event('Order accepted for room '+str(floor*100+room))
 def finish_action(self):self.active=None;self.path=[];self.retry=0;self.wait=0
 def confirm(self):
  if self.state!='WAITING_FOR_GUEST':raise ValueError('Robot is not waiting at a guest room')
  self.payload=False;self.event('Guest confirmed receipt; returning to dispatch');self.returning=True;f=floor_of(self.robot);self.queue=deque([self.nav([xyz(self.robot)[0],1750,(f-1)*400])]+self.lift(f,1)+[self.nav([-2350,2100,0])]);self.finish_action();self.state='RETURNING'
 def command(self,c):
  action=c.get('action')
  if action=='start':self.start(int(c['floor']),int(c['room']))
  elif action=='pause':self.paused=True;self.event('Paused safely')
  elif action=='resume':self.paused=False;self.error=None;self.retry=0;self.event('Resumed')
  elif action=='confirm':self.confirm()
  elif action=='cancel':
   if abs(xyz(self.car)[2]/400-round(xyz(self.car)[2]/400))>.01:raise ValueError('Elevator is between floors. Pause/resume or wait for a landing before cancelling.')
   self.queue.clear();self.active=None;self.state='CANCELLED';self.end();self.event('Cancelled; robot and elevator stopped in place')
  else:raise ValueError('Unknown action '+str(action))
 def poll(self):
  p=ROOT+'/command.json'
  if os.path.exists(p):
   try:
    c=json.load(open(p));cid=c['id']
    if cid!=self.lastcommand:
     self.lastcommand=cid
     try:self.command(c);reply={'id':cid,'ok':True}
     except Exception as e:reply={'id':cid,'ok':False,'error':str(e)}
     open(ROOT+'/ack.json','w').write(json.dumps(reply))
   except (ValueError,OSError):pass
 def tick(self,dt):
  try:
   # Preserve elapsed time during ordinary slow frames; keep collision steps small.
   remaining=max(0,min(float(dt),.5))
   while remaining>1e-6:
    step=min(remaining,1/30);self.update(step);remaining-=step
  except Exception:
   self.error=traceback.format_exc();self.paused=True;self.state='FAULT';self.end();self.event(self.error)
 def update(self,dt):
  self.poll()
  if time.monotonic()-self.lastwrite>.5:self.write();self.lastwrite=time.monotonic()
  if self.paused or self.state=='FAULT':return
  self.walk_people(dt)
  if self.state in ['IDLE','COMPLETE','CANCELLED']:return
  if not self.active:
   if not self.queue:
    self.state='COMPLETE';self.end();self.event('Delivery complete; robot returned to dispatch');return
   self.active=self.queue.popleft();self.event('Action: '+str(self.active))
  a=self.active;k=a['kind']
  if k=='load':
   self.state='LOADING';self.wait+=dt
   if self.wait>=3:self.payload=True;self.event('Parcel collected at RoomRunner dispatch');self.finish_action()
  elif k=='guest':self.state='WAITING_FOR_GUEST'
  elif k=='navigate':
   self.state='NAVIGATING';p=xyz(self.robot);goal=a['goal'];z=goal[2]
   if abs(p[2]-z)>5:raise RuntimeError('Floor mismatch; refusing cross-floor navigation')
   if math.dist(p,goal)<2:self.finish_action();return
   self.retry-=dt
   if not self.path:
    if self.retry>0:self.state='BLOCKED';return
    result=plan(p[:2],goal[:2],lambda u,v:self.clear((*u,z),(*v,z)),lambda q:self.walkable(q,z))
    self.replans+=1
    if not result:self.state='BLOCKED';self.retry=1;self.error='No collision-free path; waiting to replan';return
    self.path=[(q[0],q[1],z) for q in result];self.error=None
   q=self.path[0];dist=math.dist(p,q);step=min(dist,100*dt);n=tuple(p[i]+(q[i]-p[i])*step/max(dist,.001) for i in range(3))
   if not self.clear(p,n):
    self.path=[];self.retry=.5;self.state='BLOCKED';self.avoidance_stops+=1;self.event('Obstacle entered route; stopping and replanning');return
   self.move(self.robot,n,math.degrees(math.atan2(q[1]-p[1],q[0]-p[0]))-90);self.distance+=step
   if dist<=step+1:self.path.pop(0)
  elif k=='door':
   self.state='OPENING_DOORS' if a['open'] else 'CLOSING_DOORS';f=a['floor'];z=(f-1)*400
   if a['open'] and abs(xyz(self.car)[2]-z)>1:raise RuntimeError('Door interlock: cabin is not at this floor')
   # Check doorway occupancy before closing; robot must be fully inside or outside.
   rp=xyz(self.robot)
   if not a['open'] and abs(rp[2]-z)<100 and abs(rp[0]-1390)<75 and abs(rp[1]-1750)<150:self.state='DOOR_BLOCKED';return
   if not a['open']:
    hit=unreal.SystemLibrary.capsule_trace_single(self.world,vec((1390,1680,z+65)),vec((1390,1820,z+65)),34,60,unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,True,list(self.doors.values())+[self.car],unreal.DrawDebugTrace.NONE,True)
    if hit is not None:self.state='DOOR_BLOCKED';return
   done=True
   for side,sign in [('L',-1),('R',1)]:
    actor=self.doors[f,side];p=xyz(actor);target=1750+sign*(142 if a['open'] else 67.5);delta=target-p[1];step=max(-70*dt,min(70*dt,delta))
    if abs(delta)>.1:self.move(actor,(1390,p[1]+step,z));done=False
   if done:self.finish_action()
  elif k=='lift':
   self.state='RIDING_ELEVATOR' if a['occupied'] else 'CALLING_ELEVATOR'
   for (f,s),d in self.doors.items():
    if abs(xyz(d)[1]-(1750+(-1 if s=='L' else 1)*67.5))>.2:raise RuntimeError('Lift interlock: landing door is open')
   p=xyz(self.car);delta=a['z']-p[2]
   if abs(delta)<.1:self.finish_action();return
   dz=max(-220*dt,min(220*dt,delta))
   if a['occupied']:
    rp=xyz(self.robot)
    if not (1440<rp[0]<1600 and 1650<rp[1]<1850):raise RuntimeError('Lift interlock: robot is not fully boarded')
    nextp=(rp[0],rp[1],rp[2]+dz)
    if not self.clear(rp,nextp,[self.car]):self.state='LIFT_BLOCKED';return
    self.move(self.robot,nextp)
   self.move(self.car,(p[0],p[1],p[2]+dz))
 def walk_people(self,dt):
  # Concert only broadcasts live snapshots for objects in an active transaction.
  # Idle pedestrians previously moved solely in the controller's local editor.
  idle=self.state in ['IDLE','COMPLETE','CANCELLED']
  if idle and not self.transaction:
   self.begin();self.crowd_transaction_started=time.monotonic()
  for i,a in enumerate(self.people):
   p=xyz(a);z=p[2];west=-650+(i%3)*500;east=west+750
   if i==0:west=-1950;east=-1250
   target=self.people_goals.setdefault(a.get_name(),[east,1750 if i else 2100,z])
   if math.dist(p,target)<15:
    target=[west if target[0]>west+20 else east,1750+(45 if i%2 else -45) if i else 2100,z];self.people_goals[a.get_name()]=target
   rp=xyz(self.robot);yielding=abs(rp[2]-p[2])<100 and math.dist(p[:2],rp[:2])<110
   if yielding:
    sy=1 if p[1]>=rp[1] else -1
    target=[p[0]+(25 if p[0]>=rp[0] else -25),max(1650,min(1850,p[1]+sy*100)),z]
   distance=math.dist(p,target);step=min(distance,65*dt);q=tuple(p[j]+(target[j]-p[j])*step/max(distance,.01) for j in range(3))
   hit=unreal.SystemLibrary.capsule_trace_single(self.world,vec((p[0],p[1],p[2]+90)),vec((q[0],q[1],q[2]+90)),40,85,unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,True,([a,self.robot] if yielding and math.dist(q[:2],rp[:2])>math.dist(p[:2],rp[:2]) else [a]),unreal.DrawDebugTrace.NONE,True)
   if hit is None:
    self.move(a,q,math.degrees(math.atan2(target[1]-p[1],target[0]-p[0]))-90)
   elif not yielding:
    # Turn around instead of remaining nose-to-nose with another guest.
    self.people_goals[a.get_name()]=[west if target[0]>p[0] else east,1750+(-65 if i%2 else 65) if i else 2100,z]
  if idle and time.monotonic()-getattr(self,'crowd_transaction_started',0)>10:self.end()
 def shutdown(self):
  unreal.unregister_slate_post_tick_callback(self.callback);self.end()
def install():
 global _instance
 if _instance:_instance.shutdown()
 from copresence.roomrunner_full_delivery import FullDelivery
 labels={a.get_actor_label() for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
 if all(n in labels for n in ['RoomRunner_Fleet_02','RoomRunner_Fleet_03']):
  from copresence.roomrunner_fleet import FleetDelivery
  _instance=FleetDelivery()
 else:_instance=FullDelivery()
 return 'RoomRunner full delivery controller ready; commands in '+ROOT

def install_when_ready():
 """Restore the control listener after an agent-editor restart, once the hotel is loaded."""
 elapsed=[0.0];handle=[None]
 def wait_for_world(dt):
  if _instance is not None:
   unreal.unregister_slate_post_tick_callback(handle[0]);return
  elapsed[0]+=dt
  if elapsed[0]<5:return
  elapsed[0]=0
  # Starting idle crowd transactions before Concert joins dirties the map and
  # can block the connection. Also allow the initial session replay to settle.
  if not unreal.MultiUserClientStatics.get_multi_user_connection_status():return
  # Joining reloads the map, which briefly leaves no editor world to scan
  if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world() is None:return
  labels={a.get_actor_label() for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
  if all(n in labels for n in ['RoomRunner_DeliveryRobot','Hotel_LiftCar','Hotel_LiftDoor_10_R','RoomRunner_Fleet_03','Fleet_03_Extra_2','Fleet_03_RS_DoorR']):
   unreal.unregister_slate_post_tick_callback(handle[0]);install()
 handle[0]=unreal.register_slate_post_tick_callback(wait_for_world)
