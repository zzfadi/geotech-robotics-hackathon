"""Opt-in authoritative expert capture adapter. No task-success synthesis."""
import copy
import time

STATE_KEYS=('q_left','q_right','gap_left_m','gap_right_m')

def joint_packet(state):
    return {key:copy.deepcopy(state[key]) for key in STATE_KEYS}

class ExpertCapture:
    def __init__(self, session, table, directory, task, provenance, tabletop_label):
        import unreal
        from maker_arm_demonstration_recorder import Recorder
        from maker_arm_cup_clearance import CupEvidence
        self.session=session;self.table=table;self.evidence=CupEvidence()
        actors={a.get_actor_label():a for a in unreal.GameplayStatics.get_all_actors_of_class(session.world,unreal.StaticMeshActor)}
        self.top=actors[table.name(tabletop_label)]
        self.cup=table.props[table.v['target']['label']]
        # Warm mesh extraction before the timed5Hz sequence begins.
        self.evidence.sample(self.cup,self.top,time.monotonic(),side='left')
        self.recorder=Recorder(directory,table.captures,self.sample,task,provenance)

    def sample(self):
        import maker_arm_fleet
        # Verifies every actual runtime link against current joint feedback.
        maker_arm_fleet.verify(self.table.index,self.table.state,self.table.actors)
        stamp=time.time()
        return {'measured_joints':joint_packet(self.table.state),'measured_unix':stamp,
                'state_verified':True,'world':self.session.world.get_path_name(),
                'cup_evidence':self.evidence.sample(self.cup,self.top,time.monotonic(),side='left')}

    def before_control(self):
        if self.recorder.closed:return
        # This Slate callback observes the preceding completed physics frame,
        # before any command mutation in the current callback. All three cameras
        # follow their virtual source mounts in this same callback.
        import unreal
        if self.session.editor.get_game_world()!=self.session.world:
            raise RuntimeError('Expert authority world changed')
        for item in self.table.captures.values():
            item['actor'].set_actor_location_and_rotation(item['source'].get_actor_location(),item['source'].get_actor_rotation(),False,True)
        self.recorder.tick()

    def after_control(self, applied_state):
        if not self.recorder.closed:
            self.recorder.acknowledge(joint_packet(applied_state),time.time())
