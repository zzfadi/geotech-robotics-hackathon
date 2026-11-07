"""Soft tabletop lighting for the three simulation cameras; scoped editor task."""
import unreal
import yam_station


def setup():
    existing=yam_station.actors()
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    def actor(label,kind,location):
        a=existing.get(label)
        if a is None:a=subsystem.spawn_actor_from_class(kind,unreal.Vector(*location))
        if not isinstance(a,kind):raise ValueError('Unexpected lighting actor: '+label)
        a.modify();a.set_actor_label(label);a.set_folder_path('MakerStation/Lighting')
        return a
    for suffix in ('KeyLight','FillLight'):
        old=existing.get(yam_station.PREFIX+suffix)
        if old:
            old.modify();old.light_component.modify();old.light_component.set_intensity(0)
    for label,pos,target,power,width,height,temperature,shadows in (
        ('MakerLighting_SoftKey',(75,-95,255),(45,0,75),6000,160,120,5200,True),
        ('MakerLighting_SoftFill',(40,120,220),(35,0,90),2500,140,140,6000,False),
        ('MakerLighting_BackFill',(-65,0,200),(25,0,95),1800,100,160,5500,False)):
        a=actor(label,unreal.RectLight,pos)
        a.set_actor_rotation(unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*pos),unreal.Vector(*target)),True)
        c=a.get_component_by_class(unreal.RectLightComponent);c.modify()
        c.set_mobility(unreal.ComponentMobility.MOVABLE)
        c.set_editor_property('intensity_units',unreal.LightUnits.LUMENS)
        c.set_intensity(power);c.set_editor_property('source_width',width);c.set_editor_property('source_height',height)
        c.set_editor_property('attenuation_radius',700)
        c.set_editor_property('use_temperature',True);c.set_temperature(temperature)
        c.set_cast_shadows(shadows)
    sky=actor('MakerLighting_Ambient',unreal.SkyLight,(0,0,220))
    c=sky.get_component_by_class(unreal.SkyLightComponent);c.modify()
    c.set_mobility(unreal.ComponentMobility.MOVABLE)
    c.set_editor_property('source_type',unreal.SkyLightSourceType.SLS_SPECIFIED_CUBEMAP)
    c.set_cubemap(unreal.load_asset('/Engine/MapTemplates/Sky/DaylightAmbientCubemap'))
    c.set_intensity(.7)
    volume=actor('MakerLighting_Exposure',unreal.PostProcessVolume,(45,0,150))
    volume.set_editor_property('unbound',True)
    settings=volume.get_editor_property('settings')
    values={'auto_exposure_method':unreal.AutoExposureMethod.AEM_MANUAL,
            'auto_exposure_apply_physical_camera_exposure':True,
            'auto_exposure_bias':0.,'camera_iso':400.,'camera_shutter_speed':60.,
            'depth_of_field_fstop':4.,'bloom_intensity':.1}
    for key,value in values.items():
        settings.set_editor_property('override_'+key,True)
        settings.set_editor_property(key,value)
    volume.set_editor_property('settings',settings)
    return {'rect_lights':3,'ambient':'daylight cubemap','exposure':'fixed EV100 about 7.9','scope':'simulation tabletop'}
