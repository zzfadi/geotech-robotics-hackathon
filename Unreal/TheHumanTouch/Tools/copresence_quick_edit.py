"""Fixed, data-only scene operations used by the CoPresence MCP server.

Call participants supply actor/material references, never executable Python. These
scripts still execute through CoPresence's transaction and save guards.
"""
import json
import math
import re


def text(value, name, maximum=1024, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum:
        raise ValueError(f"Invalid {name}")
    if any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {name}")
    return value


def vector(value, name, scalar=False):
    if value is None:
        return None
    if scalar and isinstance(value, (float, int)) and not isinstance(value, bool):
        value = [value] * 3
    if (not isinstance(value, list) or len(value) != 3
            or any(isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) for n in value)):
        raise ValueError(f"{name} must contain three finite numbers")
    return value


def edit_arguments(actor, request, material_path, material_slot, component, location, rotation, scale):
    args = dict(actor=text(actor, 'actor'), request=text(request, 'request', 2000),
                material_path=text(material_path, 'material path', empty=True),
                material_slot=text(material_slot, 'material slot', 200, empty=True),
                component=text(component, 'component', empty=True),
                location=vector(location, 'location'), rotation=vector(rotation, 'rotation'),
                scale=vector(scale, 'scale', scalar=True))
    transforms = any(args[k] is not None for k in ('location', 'rotation', 'scale'))
    if bool(material_path) == transforms:
        raise ValueError('Choose a material change OR a transform change')
    if material_path and (not re.fullmatch(r'/(?:Game|Engine|CoPresence)/[A-Za-z0-9_./-]+', material_path)
                          or '..' in material_path or '//' in material_path or '/./' in material_path):
        raise ValueError('Use an existing project or engine material asset path')
    if not material_path and (material_slot or component):
        raise ValueError('Material slot and component are only used for material changes')
    return args


def script(source, args):
    # repr(JSON) prevents actor labels and paths becoming Python expressions.
    return 'import json\nargs = json.loads(' + repr(json.dumps(args)) + ')\n' + source


INSPECT = '''
import re
def normalized(name):
    return re.sub(r'^(?:sm|s|bp)_', '', name, flags=re.I).replace('_', ' ').casefold()
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
query = normalized(args['query'])
matches = [a for a in actors if query in normalized(a.get_actor_label()) or args['query'] == a.get_path_name()]
matches.sort(key=lambda a: (normalized(a.get_actor_label()) != query, len(a.get_actor_label()), a.get_path_name()))
rows = []
for actor in matches[:args['limit']]:
    components = []
    for c in actor.get_components_by_class(unreal.MeshComponent):
        slots = list(c.get_material_slot_names())
        components.append({'name': c.get_name(), 'path': c.get_path_name(), 'materials': [
            {'slot': str(slots[i]) if i < len(slots) else str(i), 'index': i,
             'material': m.get_path_name() if m else None} for i, m in enumerate(c.get_materials())]})
    p = actor.get_actor_location()
    rows.append({'label': actor.get_actor_label(), 'path': actor.get_path_name(),
                 'location': [p.x, p.y, p.z], 'components': components})
materials = []
if args['material_query']:
    needle = args['material_query'].casefold()
    materials = [p for p in unreal.EditorAssetLibrary.list_assets('/Game/Materials', recursive=True)
                 if needle in p.rsplit('/', 1)[-1].casefold()]
    materials.sort(key=lambda p: (len(p.rsplit('/', 1)[-1]), p))
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
result = {'map': world.get_path_name().split('.')[0], 'matches': rows, 'total_matches': len(matches),
          'materials': materials[:args['limit']], 'total_materials': len(materials)}
'''


MATERIAL = '''
from copresence import tracking
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
matches = [a for a in actors if args['actor'] in (a.get_actor_label(), a.get_path_name())]
if len(matches) != 1:
    raise ValueError('Actor must match exactly one label or path; use inspect_scene first')
actor = matches[0]
if tracking.is_local_only(actor):
    raise ValueError('Local avatars cannot be edited with quick_edit')
components = list(actor.get_components_by_class(unreal.MeshComponent))
if args['component']:
    components = [c for c in components if args['component'] in (c.get_name(), c.get_path_name())]
if len(components) != 1:
    raise ValueError('Specify one mesh component from inspect_scene')
component = components[0]
slots = list(component.get_material_slot_names())
if args['material_slot']:
    indices = [i for i, name in enumerate(slots) if str(name) == args['material_slot']]
    if len(indices) != 1:
        raise ValueError('Material slot must match exactly one slot name from inspect_scene')
    index = indices[0]
elif component.get_num_materials() == 1:
    index = 0
else:
    raise ValueError('Specify the material slot; this mesh has multiple finishes')
material = unreal.load_asset(args['material_path'])
if not isinstance(material, unreal.MaterialInterface):
    raise ValueError('The asset is not an existing material')
previous = component.get_material(index)
before = tracking.actor_state(actor)
before['materials'] = [m.get_path_name() if m else None for m in component.get_materials()]
changed = previous != material
if changed:
    actor.modify()
    component.modify()
    component.set_material(index, material)
if component.get_material(index) != material:
    raise RuntimeError('The material assignment did not take effect')
after = tracking.actor_state(actor)
after['materials'] = [m.get_path_name() if m else None for m in component.get_materials()]
result = {'actor': actor.get_path_name(), 'slot': str(slots[index]), 'material': material.get_path_name(),
          'changed': changed, 'before': before, 'after': after}
'''
