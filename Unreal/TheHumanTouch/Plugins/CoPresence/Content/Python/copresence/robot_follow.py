"""Opt-in local viewport follower. Reads replicated actors; never moves the robot."""
import unreal
import json, threading, math
from urllib.request import urlopen
from copresence.robot_follow_math import SmoothRig
_instance = None

class Follow:
    def __init__(self, label, automatic=False):
        self.label, self.robot, self.camera = label, None, None
        self.rig = SmoothRig()
        self.automatic=automatic
        self.active=False
        self.suppressed=False
        self.last_camera=None
        self.closing=threading.Event()
        if automatic:threading.Thread(target=self.poll_mission,daemon=True).start()
        # Replicated poses arrive in steps. Motion blur magnifies each correction.
        world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        self.old_blur=unreal.SystemLibrary.get_console_variable_int_value('r.MotionBlurQuality')
        unreal.SystemLibrary.execute_console_command(world,'r.MotionBlurQuality 0')
        from copresence import agents_hud
        agents_hud._state['following']=None
        self.handle = unreal.register_slate_post_tick_callback(self.tick)

    def poll_mission(self):
        while not self.closing.is_set():
            try:
                with urlopen("http://100.69.88.8:8093/api/state",timeout=2) as response:data=json.load(response)
                telemetry=json.loads(data["messages"].get("/copresence/robot_telemetry",{}).get("data","{}"))
                state=telemetry.get("delivery",{}).get("state","IDLE")
                self.active=data.get("state")=="live" and state not in {"IDLE","DOCKED","FAULT","CANCELLED"}
            except Exception:self.active=False
            self.closing.wait(0.5)

    def tick(self, dt):
        try:
            if self.automatic:
                if not self.active:
                    self.last_camera=None
                    self.suppressed=False
                    self.rig=SmoothRig()
                    return
                if self.suppressed:return
                if self.last_camera is not None:
                    position,rotation=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_level_viewport_camera_info()
                    old_position,old_rotation=self.last_camera
                    if math.dist(position.to_tuple(),old_position.to_tuple())>2 or abs(rotation.yaw-old_rotation.yaw)>1 or abs(rotation.pitch-old_rotation.pitch)>1:
                        self.suppressed=True
                        return
            if self.robot is None:
                self.robot = next((a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
                                   if a.get_actor_label() == self.label), None)
                if self.robot is None:
                    return
                unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_game_view(True)
            p = self.robot.get_actor_location()
            camera, target = self.rig.step(p.to_tuple(), self.robot.get_actor_rotation().yaw, dt)
            self.camera = unreal.Vector(*camera)
            rotation=unreal.MathLibrary.find_look_at_rotation(self.camera,unreal.Vector(*target))
            unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).set_level_viewport_camera_info(self.camera,rotation)
            # Agent editors render on demand. Refresh after moving the spectator
            # without repeatedly removing the editor's realtime override.
            unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_invalidate_viewports()
            self.last_camera=(self.camera,rotation)
        except Exception:
            self.robot, self.camera = None, None
            self.rig = SmoothRig()

    def stop(self):
        self.closing.set()
        unreal.unregister_slate_post_tick_callback(self.handle)
        world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        unreal.SystemLibrary.execute_console_command(world,f'r.MotionBlurQuality {self.old_blur}')

def start(label='RoomRunner_DeliveryRobot', automatic=False):
    global _instance
    if _instance:
        _instance.stop()
    _instance = Follow(label, automatic)
    return 'Local viewport following '+label

def stop():
    global _instance
    if _instance:
        _instance.stop()
    _instance = None
