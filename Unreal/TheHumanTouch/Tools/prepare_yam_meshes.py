"""Convert pinned binary STL meshes to OBJ in right-handed centimetres/link-local coordinates."""
import struct
from pathlib import Path
from yam_kinematics import model, origin, MODEL

def prepare(destination):
    destination = Path(destination); destination.mkdir(parents=True,exist_ok=True)
    for link in model().findall('link'):
        visual=link.find('visual'); t=origin(visual.find('origin'))
        mesh=visual.find('geometry/mesh'); scale=list(map(float,mesh.get('scale','1 1 1').split()))
        data=(MODEL.parent/mesh.get('filename')).read_bytes()
        count=struct.unpack_from('<I',data,80)[0]
        if len(data) != 84+50*count: raise ValueError('Expected binary STL')
        with (destination/(link.get('name')+'.obj')).open('w') as f:
            f.write('# I2RT MIT mesh; visual origin baked; RH cm XYZ; Unreal OBJ importer reflects Y\n')
            for n in range(count):
                v=struct.unpack_from('<12f',data,84+50*n)
                for k in range(3):
                    p=[v[3+3*k+i]*scale[i] for i in range(3)]
                    p=[100*(sum(t[i][j]*p[j] for j in range(3))+t[i][3]) for i in range(3)]
                    f.write('v '+' '.join(f'{x:.8g}' for x in p)+'\n')
                # Unreal OBJ importer performs the handedness conversion.
                f.write(f'f {3*n+1} {3*n+2} {3*n+3}\n')
    return destination

if __name__ == '__main__':
    print(prepare(MODEL.parents[2]/'Saved/CoPresenceImports/YAM'))
