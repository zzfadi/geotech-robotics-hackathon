"""Opt-in read-only runtime collision ledger for recorded-action diagnostics.

Never moves actors or changes physics parameters. Geometric overlaps are not
solver contacts. Optional OnComponentHit logs raw impulses only when events are
already enabled; never infers a force from render or Slate time.
"""
import json
import math
import time
from pathlib import Path

_active=None


def vector(v):
    return [v.x,v.y,v.z]


def transform(t):
    q=t.rotation
    return {'position_cm':vector(t.translation),'rotation_xyzw':[q.x,q.y,q.z,q.w],'scale':vector(t.scale3d)}


def replay_schedule(requests, timeline, last_cycle=15):
    """Derive command onset from original recorded command sequence, not API latency."""
    if not timeline:raise ValueError('Missing recorded image timeline')
    origin=float(timeline[0]['capture_requested_unix']);result=[]
    for cycle in range(1,last_cycle+1):
        request=next((x for x in requests if x['call']==cycle and x.get('returned_action')),None)
        if request is None:raise ValueError('Missing real returned action')
        first=next((x for x in timeline if x['command_seq']>=cycle),None)
        if first is None:raise ValueError('Missing recorded command onset')
        # The first timeline frame carrying this seq bounds actual onset from
        # above. API completion bounds it below; exact onset was not logged.
        lower=float(request['finished_unix'])-origin
        upper=float(first['capture_requested_unix'])-origin
        if not all(math.isfinite(v) for v in (lower,upper)) or upper<lower:
            raise ValueError('Inconsistent command timing')
        result.append({'seq':cycle,'earliest_seconds':lower,'latest_seconds':upper,
                       'schedule_seconds':lower,'timing_scope':'API-return lower bound; exact actuator onset unavailable'})
    return result


class CollisionAudit:
    def __init__(self,directory,index):
        import unreal
        import maker_arm_fleet_runtime
        session=maker_arm_fleet_runtime._session
        if session is None or session.phase!='ready':raise RuntimeError('Ready authoritative runtime required')
        if session.world != session.editor.get_game_world():raise RuntimeError('Not the authoritative runtime world')
        self.session=session;self.world=session.world
        self.table=next(t for t in session.tables if t.index==index)
        self.root=Path(directory);self.root.mkdir(parents=True,exist_ok=False)
        self.file=(self.root/'ticks.jsonl').open('w')
        self.events=(self.root/'native-hits.jsonl').open('w')
        self.bindings=[];self.closed=False;self.ticks=0;self.callback=None;self.last_tick=None;self.gaps=[];self.collection_ms=[]
        self.types=[getattr(unreal.ObjectTypeQuery,'OBJECT_TYPE_QUERY'+str(i)) for i in range(1,7)]
        self.meta={'index':index,'world':self.world.get_path_name(),'scope':'zero-API recorded-action diagnostic, not policy success',
                   'geometric_sampling':'Slate callback after runtime control; preceding physics result plus current kinematic transforms',
                   'native_impulse_units':'raw UE normal impulse (kg cm/s); no force conversion',
                   'native_event_setup':{},'collision_parameters_changed':False}
        for name,actor in self.table.props.items():
            component=actor.static_mesh_component
            setup={'bound':False,'notify_rigid_body_collision':None}
            try:
                setup['notify_rigid_body_collision']=bool(component.get_editor_property('body_instance').get_editor_property('notify_rigid_body_collision'))
                def hit(hit_component,other_actor,other_component,normal_impulse,result,source=name):
                    if self.closed:return
                    data={'unix':time.time(),'cup':source,'world':self.world.get_path_name(),
                          'other_actor':other_actor.get_actor_label() if other_actor else None,
                          'other_component_path':other_component.get_path_name() if other_component else None,
                          'normal_impulse':vector(normal_impulse)}
                    self.events.write(json.dumps(data)+'\n');self.events.flush()
                component.on_component_hit.add_callable(hit)
                self.bindings.append((component,hit));setup['bound']=True
            except Exception as error:setup['error']=str(error)
            setup['events_observable']=setup['bound'] and setup['notify_rigid_body_collision'] is True
            self.meta['native_event_setup'][name]=setup
        # Nonmutating positive control: query the existing cup shape at the
        # tabletop centre. This never changes the actual cup transform.
        top_name=self.table.name('Dreamlabs_YAM_Table')
        top=next(a for a in unreal.GameplayStatics.get_all_actors_of_class(self.world,unreal.Actor) if a.get_actor_label()==top_name)
        cup=self.table.props['MakerStation_Prop_Cup_03'];query=cup.get_actor_transform()
        query.translation=top.get_actor_location()
        hits=unreal.SystemLibrary.component_overlap_components(cup.static_mesh_component,query,self.types,unreal.PrimitiveComponent,[cup]) or []
        self.meta['overlap_positive_control']=any(c.get_owner()==top for c in hits)
        if not self.meta['overlap_positive_control']:
            self.meta['error']='Overlap positive control failed';self.stop();raise RuntimeError(self.meta['error'])
        self.meta['initial_components']=self.nearby_components()
        (self.root/'audit.json').write_text(json.dumps(self.meta,indent=2))
        self.callback=unreal.register_slate_post_tick_callback(self.tick)

    def component(self,c):
        import unreal
        owner=c.get_owner()
        out={'actor':owner.get_actor_label(),'actor_path':owner.get_path_name(),'component_path':c.get_path_name(),
             'actor_transform':transform(owner.get_actor_transform()),'component_transform':transform(c.get_world_transform()),
             'collision_enabled':str(c.get_collision_enabled()),'collision_profile':str(c.get_collision_profile_name()),
             'simulating':c.is_simulating_physics(),'gravity_enabled':c.is_gravity_enabled(),
             'velocity_cm_s':vector(c.get_physics_linear_velocity()),'visible':c.is_visible()}
        if isinstance(c,unreal.StaticMeshComponent):
            lo,hi=c.get_local_bounds();out.update(mesh=c.static_mesh.get_path_name() if c.static_mesh else None,
                                                local_bounds_cm=[vector(lo),vector(hi)])
        return out

    def nearby_components(self):
        import unreal
        base=self.table.bases['left'];out=[]
        for actor in unreal.GameplayStatics.get_all_actors_of_class(self.world,unreal.Actor):
            p=actor.get_actor_location()
            if math.dist((p.x,p.y,p.z),base)>250:continue
            for c in actor.get_components_by_class(unreal.PrimitiveComponent):out.append(self.component(c))
        return out

    def tick(self,delta):
        import unreal
        if self.closed:return
        try:
            entered=time.monotonic()
            gap=None if self.last_tick is None else entered-self.last_tick
            self.last_tick=entered
            if gap is not None:self.gaps.append(gap)
            if self.session.phase!='ready' or self.session.editor.get_game_world()!=self.world:self.stop();return
            cups={}
            for name,actor in self.table.props.items():
                c=actor.static_mesh_component
                hits=unreal.SystemLibrary.component_overlap_components(c,c.get_world_transform(),self.types,unreal.PrimitiveComponent,[actor]) or []
                cups[name]={'component':self.component(c),'overlap_components':[self.component(hit) for hit in hits],
                            'geometric_overlap_snapshot_complete':True}
            robots={name:self.component(actor.static_mesh_component) for name,actor in self.table.actors.items()}
            proxies={name:self.component(actor.static_mesh_component) for name,actor in self.table.proxies.items()}
            row={'unix':time.time(),'monotonic':time.monotonic(),'frame':self.ticks,'world':self.world.get_path_name(),
                 'command_seq':self.table.command_seq,'motion_index':self.table.motion['index'] if self.table.motion else None,
                 'state':self.table.state,'cups':cups,'robot_components':robots,'proxy_components':proxies}
            row['callback_gap_seconds']=gap
            row['collection_duration_ms']=(time.monotonic()-entered)*1000
            self.collection_ms.append(row['collection_duration_ms'])
            self.file.write(json.dumps(row)+'\n');self.file.flush();self.ticks+=1
        except Exception as error:
            self.meta['error']=str(error);self.stop()

    def stop(self):
        import unreal
        if self.closed:return
        self.closed=True
        if self.callback is not None:unreal.unregister_slate_post_tick_callback(self.callback)
        for component,callback in self.bindings:
            try:component.on_component_hit.remove_callable(callback)
            except Exception:pass
        self.file.close();self.events.close();self.meta['ticks']=self.ticks
        self.meta['timing']={'mean_callback_gap_seconds':sum(self.gaps)/len(self.gaps) if self.gaps else None,
                             'max_callback_gap_seconds':max(self.gaps) if self.gaps else None,
                             'max_collection_duration_ms':max(self.collection_ms) if self.collection_ms else None,
                             'mean_collection_duration_ms':sum(self.collection_ms)/len(self.collection_ms) if self.collection_ms else None,
                             'scope':'Instrumentation overhead measured; identical replay physics timing is not assumed'}
        (self.root/'audit.json').write_text(json.dumps(self.meta,indent=2))


def start(directory,index=4):
    global _active
    if _active is not None and not _active.closed:raise RuntimeError('Collision audit already active')
    _active=CollisionAudit(directory,index)
    return {'world':_active.meta['world'],'native_event_setup':_active.meta['native_event_setup']}


def stop():
    if _active is not None:_active.stop()
