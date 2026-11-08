"""Scene-only Table10 human-interaction bay. Never sends physical SDK commands.

Distinct labels/tags keep this bay outside the nine-table Dream worker fleet.
Uses original pinned CAD assets and the accepted room geometry/materials.
"""
INDEX=9
PREFIX='MakerInteraction_T09__'
TAG='MakerInteractionTable10'
HOME_Q=[-8.341015791761527e-08,-1.3267562769368857,1.5947274948757348,-1.4680311465744582,1.6235197063895467e-08,-8.180590968960631e-08]


def build():
    import unreal,maker_arm_fleet as fleet,maker_arm_grid as grid
    import maker_arm_grid_scene as scene,maker_arm_wall_numbers as numbers
    fleet._check_idle()
    subsystem=fleet._subsystem();all_actors=fleet._actors()
    existing=[a for n,a in all_actors.items() if n.startswith(PREFIX)]
    if existing:return {'already_present':True,'actors':len(existing),'prefix':PREFIX}
    if any(n.startswith('MakerFleet_T09__') for n in all_actors):
        raise RuntimeError('Reserved staging prefixT09 already exists; preserve it')
    source={n:a for n,a in fleet.sources().items() if not n.startswith('MakerStation_Prop_')}
    if not all(n in source for n in ('Dreamlabs_YAM_Table','MakerArm_left_base_link','MakerArm_right_base_link','MakerCamera_Overview','MakerEnvironment_Floor')):
        raise RuntimeError('Missing original source station geometry')
    made=fleet._duplicate_table(INDEX,source,local=False)
    for side in ('left','right'):fleet.set_pose(INDEX,side,HOME_Q,.1,made)
    fleet.tune_lights(INDEX,made)
    scene.migrate_table(INDEX)
    scene.number_sticker(INDEX)
    numbers.apply(INDEX)
    created=[a for a in subsystem.get_all_level_actors() if a.get_actor_label().startswith('MakerFleet_T09__')]
    for actor in created:
        actor.modify();suffix=actor.get_actor_label().split('__',1)[1]
        actor.set_actor_label(PREFIX+suffix)
        actor.set_folder_path('MakerInteraction/Table10')
        actor.set_editor_property('tags',[unreal.Name('MakerInteraction'),unreal.Name(TAG)])
    assert not any(a.get_actor_label().startswith('MakerFleet_T09__') for a in subsystem.get_all_level_actors())
    return {'created_actors':len(created),'prefix':PREFIX,'display_number':10,
            'offset_cm':list(grid.offset(INDEX)),'table_center_cm':[2505,0,72],
            'arm_bases_cm':{'left':[2480,-40,76],'right':[2480,40,76]},
            'joints_rad':HOME_Q,'jaw_gap_m':.1,'physical_sdk_commands_sent':0,
            'scope':'Scene and kinematic CAD only; contact-to-hardware link is a separate controller'}
