"""Read-only mesh-vertex clearance and shape-overlap evidence for expert demos.

No arm/object setters, solver forces, attachment, or policy inference. Overlaps
are geometric observations, not solver impulses or calibrated contact forces.
"""
import math


def lowest_clearance(world_cup_vertices, world_table_vertices):
    """Exact minimum of the polygon mesh; a linear Z reaches extrema at vertices.

    Caller supplies an upright rectangular tabletop mesh, not table legs. The
    upper face must be horizontal; unsupported tilted tables fail closed.
    """
    if not world_cup_vertices or not world_table_vertices:
        raise ValueError('Missing actual mesh vertices')
    if not all(math.isfinite(v) for p in [*world_cup_vertices,*world_table_vertices] for v in p):
        raise ValueError('Nonfinite transformed geometry')
    top=max(p[2] for p in world_table_vertices)
    return min(p[2] for p in world_cup_vertices)-top,top


class CupEvidence:
    """One instance per controlled runtime; caches actual render LOD0 vertices."""
    def __init__(self):
        self.vertices={}
        self.hold_started=None
        self.last_time=None

    def _vertices(self,component):
        import unreal
        mesh=component.static_mesh
        if mesh is None:raise ValueError('Missing static mesh')
        key=mesh.get_path_name()
        if key not in self.vertices:
            dynamic=unreal.DynamicMesh()
            lod=unreal.GeometryScriptMeshReadLOD(lod_type=unreal.GeometryScriptLODType.RENDER_DATA,lod_index=0)
            _,outcome=unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(mesh,dynamic,unreal.GeometryScriptCopyMeshFromAssetOptions(),lod)
            if outcome!=unreal.GeometryScriptOutcomePins.SUCCESS:raise RuntimeError('Actual mesh extraction failed')
            _,positions,_=unreal.GeometryScript_MeshQueries.get_all_vertex_positions(dynamic,True)
            vertices=unreal.GeometryScript_List.convert_vector_list_to_array(positions)
            if not vertices:raise RuntimeError('Actual mesh vertices unavailable')
            self.vertices[key]=list(vertices)
        transform=component.get_world_transform()
        return [(p.x,p.y,p.z) for p in (transform.transform_location(v) for v in self.vertices[key])]

    def sample(self,cup,table,now,side='left'):
        import unreal
        if side not in ('left','right') or not math.isfinite(now):raise ValueError('Invalid evidence side/time')
        c=cup.static_mesh_component;tc=table.static_mesh_component
        up=tc.get_up_vector()
        if abs(up.x)>1e-5 or abs(up.y)>1e-5 or up.z<.99999:
            raise ValueError('Tabletop must be horizontal for clearance evidence')
        clearance,top=lowest_clearance(self._vertices(c),self._vertices(tc))
        velocity=c.get_physics_linear_velocity();cup_up=c.get_up_vector()
        speed=math.sqrt(velocity.x**2+velocity.y**2+velocity.z**2)
        tilt=math.degrees(math.acos(max(-1.,min(1.,cup_up.z))))
        parent=cup.get_attach_parent_actor()
        result={'lowest_point_clearance_cm':clearance,'tabletop_world_z_cm':top,
                'tilt_degrees':tilt,'speed_cm_s':speed,'physics_simulated':c.is_simulating_physics(),
                'gravity_enabled':c.is_gravity_enabled(),'attached_to':parent.get_actor_label() if parent else None,
                'geometry_source':'Actual render LOD0 vertices transformed by current component world transform',
                'support_snapshot_complete':False,'bilateral_jaw_contacts':False,
                'other_support_contacts':None,'contact_provenance':'shape_overlap_not_solver_impulses'}
        try:
            types=[getattr(unreal.ObjectTypeQuery,'OBJECT_TYPE_QUERY'+str(i)) for i in range(1,7)]
            hits=unreal.SystemLibrary.component_overlap_components(c,c.get_world_transform(),types,unreal.PrimitiveComponent,[cup]) or []
            labels=sorted(set(x.get_owner().get_actor_label() for x in hits))
            # This controlled diagnostic targets original Table1 only. Do not
            # accept another room's similarly named proxy as supporting this cup.
            expected={'FleetProxy_MakerArm_'+side+'_gripper_left','FleetProxy_MakerArm_'+side+'_gripper_right'}
            result.update(support_snapshot_complete=True,bilateral_jaw_contacts=expected.issubset(labels),
                          other_support_contacts=[x for x in labels if x not in expected],overlap_labels=labels)
        except Exception as error:
            result['support_error']=str(error)
        eligible=(clearance>=10 and tilt<=15 and speed<=10 and result['physics_simulated'] and result['gravity_enabled']
                  and parent is None and result['support_snapshot_complete'] and result['bilateral_jaw_contacts']
                  and not result['other_support_contacts'])
        # A missed observation breaks continuity; no invented hold through gaps.
        if self.last_time is not None and (now<=self.last_time or now-self.last_time>.3):self.hold_started=None
        self.last_time=now
        if not eligible:self.hold_started=None
        elif self.hold_started is None:self.hold_started=now
        result['mesh_clearance_supported_hold_seconds']=0. if self.hold_started is None else now-self.hold_started
        result['mechanical_candidate_eligible']=eligible
        result['grasp_verified']=False
        return result
