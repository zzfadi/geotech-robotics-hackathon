"""EXPERIMENTAL hollow cup collision candidate; NOT the active production asset.

Static cavity sweeps passed, but resting/grasp dynamics regressed; baseline20
hulls were restored. Do not automatically apply this candidate.

Deterministic hollow cup collision, preserving the authored open cavity.

Thirty-two convex wall sectors + one continuous flat bottom. Outer facets match
all64 visual-ring vertices. Inner chords intrude <=0.0183cm (0.183mm) at rim;
this is explicit approximation, not manufacturer-calibrated collision.
"""
import math


def _prism(bottom,top):
    n=len(bottom)
    if len(top)!=n:raise ValueError('Ring sizes differ')
    vertices=bottom+top;triangles=[]
    for i in range(1,n-1):
        triangles.extend(((0,i+1,i),(n,n+i,n+i+1)))
    for i in range(n):
        j=(i+1)%n;triangles.extend(((i,j,n+j),(i,n+j,n+i)))
    return {'vertices_cm':vertices,'triangles':triangles}


def cup_hulls(sectors=32):
    if sectors!=32:raise ValueError('Validated configuration uses32 sectors/64 outer facets')
    def point(r,a,z):return [r*math.cos(a),r*math.sin(a),z]
    # One uninterrupted base; no fragmented VHACD bottom pieces or seams.
    bottom=[point(3.,i*math.pi/32,-6.) for i in range(64)]
    top=[point(3.025,i*math.pi/32,-5.7) for i in range(64)]
    pieces=[_prism(bottom,top)]
    for i in range(sectors):
        a=i*2*math.pi/sectors;b=(i+1)*2*math.pi/sectors
        def ring(outer,inner,z):
            return [point(outer,a,z),point(outer,(a+b)/2,z),point(outer,b,z),point(inner,b,z),point(inner,a,z)]
        pieces.append(_prism(ring(3.025,2.8,-5.7),ring(4.,3.8,6.)))
    return pieces


def contains_point(piece,point,tolerance=1e-8):
    """Offline convex-piece point test for topology regression probes."""
    vertices=piece['vertices_cm'];center=[sum(p[i] for p in vertices)/len(vertices) for i in range(3)]
    for tri in piece['triangles']:
        a,b,c=[vertices[i] for i in tri];u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
        normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        length=math.sqrt(sum(x*x for x in normal))
        if length<1e-12:continue
        sign=-1 if sum(normal[i]*(center[i]-a[i]) for i in range(3))>0 else 1
        distance=sum(sign*normal[i]*(point[i]-a[i]) for i in range(3))/length
        if distance>tolerance:return False
    return True


def build_collision():
    """Create transient shapes only; does not alter an asset or scene."""
    import unreal
    mesh=unreal.DynamicMesh()
    for piece in cup_hulls():
        buffers=unreal.GeometryScriptSimpleMeshBuffers()
        buffers.vertices=[unreal.Vector(*p) for p in piece['vertices_cm']]
        buffers.triangles=[unreal.IntVector(*t) for t in piece['triangles']]
        unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(mesh,buffers)
    options=unreal.GeometryScriptCollisionFromMeshOptions()
    for name,value in {'method':unreal.GeometryScriptCollisionGenerationMethod.CONVEX_HULLS,
                       'auto_detect_spheres':False,'auto_detect_boxes':False,'auto_detect_capsules':False,
                       'simplify_hulls':False,'max_convex_hulls_per_mesh':1,'min_thickness':.001,
                       'remove_fully_contained_shapes':False,'max_shape_count':0,'emit_transaction':False}.items():
        options.set_editor_property(name,value)
    collision=unreal.GeometryScript_Collision.generate_collision_from_mesh(mesh,options)
    count=unreal.GeometryScript_Collision.get_simple_collision_shape_count(collision)
    if count!=33:raise RuntimeError('Expected33 separate hollow cup hulls, got '+str(count))
    return collision


def apply_experimental(mesh):
    """Explicit guarded asset mutation only while no SIE; never called on import."""
    import unreal
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world() is not None:
        raise RuntimeError('Stop physics before replacing shared collision geometry')
    collision=build_collision()
    mesh.modify()
    options=unreal.GeometryScriptSetSimpleCollisionOptions(emit_transaction=False)
    unreal.GeometryScript_Collision.set_simple_collision_of_static_mesh(collision,mesh,options)
    unreal.EditorAssetLibrary.set_metadata_tag(mesh,'CupCollision','32 deterministic wall sectors +1continuousbase; open cavity; inner chord error<=0.183mm; assumed dimensions; uncalibrated dynamics')
    return {'hulls':33,'visual_mesh_changed':False,'open_cavity':True,'inner_chord_error_max_cm':3.8*(1-math.cos(math.pi/32))}
