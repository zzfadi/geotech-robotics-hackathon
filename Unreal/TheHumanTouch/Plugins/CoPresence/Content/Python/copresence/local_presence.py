"""Transient self marker for agent streams; never serialized or replicated."""
import uuid
import unreal
from copresence import agent_editor, tracking
actor = None
world_path = None

def _isolate_name(marker):
    # Concert reserves BP_AgentGhost_C_N names for its own presence actors.
    # A local transient marker must never occupy that namespace.
    if not marker.get_name().startswith("CoPresenceSelf_"):
        if not marker.rename("CoPresenceSelf_" + uuid.uuid4().hex):
            raise RuntimeError("Could not isolate local presence actor name")

def ensure(world, ghost_class):
    global actor, world_path
    if not agent_editor.is_agent_editor(): return
    # EditorActorSubsystem omits transient presence actors. Enumerate their class
    # even when our cached actor is valid, so hot reload cannot leave duplicates.
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    existing = [a for a in unreal.GameplayStatics.get_all_actors_of_class(world, ghost_class)
                if a.actor_has_tag('CoPresenceSelf')]
    if existing:
        actor = actor if actor in existing else existing[0]
        for duplicate in existing:
            if duplicate != actor: subsystem.destroy_actor(duplicate)
        _isolate_name(actor)
        return
    actor = subsystem.spawn_actor_from_class(
        ghost_class, unreal.Vector(-200, -200, 300), transient=True)
    _isolate_name(actor)
    actor.tags = [unreal.Name(tracking.LOCAL_ONLY_TAG), unreal.Name('CoPresenceSelf')]
    actor.set_actor_label('CoPresence_LocalAgentEyes')
    for component in actor.get_components_by_class(unreal.TextRenderComponent):
        if component.get_name() == 'Name': component.set_text(tracking.agent_name())
    actor.set_actor_enable_collision(False)
    world_path = world.get_path_name()

def update(dt):
    if actor is None or not unreal.SystemLibrary.is_valid(actor): return
    from copresence import meet_build_camera, ghost_labels
    focus = meet_build_camera.status.get('focus')
    target = ghost_labels._work_anchors.get(tracking.agent_name())
    if focus and tracking.current_task.get('id'):
        target = unreal.Vector(focus[0]-200, focus[1]-200, max(focus[2]+220, 260))
    # A task can edit another agent's actors or only inspect the scene. In
    # either case there may be no owned work anchor. Keep the local presenter
    # inside the camera view, including when an old anchor is now off screen.
    camera, rotation = tracking.camera_info()
    forward = rotation.get_forward_vector()
    right = rotation.get_right_vector()
    up = rotation.get_up_vector()
    if target is not None:
        offset = target - camera
        depth = offset.dot(forward)
        visible = (depth > 150 and abs(offset.dot(right)) < depth * .55
                   and abs(offset.dot(up)) < depth * .25)
    else:
        visible = False
    if not visible:
        target = camera + forward * 650 - right * 250 - up * 100
    if target is not None:
        here = actor.get_actor_location()
        actor.set_actor_location(here + (target-here)*min(dt*3, 1), False, False)
