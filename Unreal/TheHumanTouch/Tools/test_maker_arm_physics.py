import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from maker_arm_physics import description, inertial_data, matmul, transpose, principal_inertia
from maker_arm_model import prepare

class PhysicsDataTests(unittest.TestCase):
    def test_inertia_rotation_reflection_and_units(self):
        # Rotate the source's X/Y principal moments by 45 degrees around Z,
        # then reflect Y. Off-diagonal sign must reverse without changing eigenvalues.
        node=ET.fromstring('''<inertial><origin xyz=".01 .02 .03" rpy="0 0 .7853981633974483"/>
        <mass value="2"/><inertia ixx=".001" iyy=".002" izz=".0025" ixy="0" ixz="0" iyz="0"/></inertial>''')
        data=inertial_data(node)
        self.assertEqual(data['mass_kg'],2)
        self.assertEqual(data['com_cm'],[1,-2,3])
        self.assertAlmostEqual(data['inertia_kg_cm2'][0][1],5)
        for value,expected in zip(data['principal_moments_kg_cm2'],[10,20,25]):
            self.assertAlmostEqual(value,expected)
        axes=data['principal_axes_columns']
        diagonal=[[data['principal_moments_kg_cm2'][i] if i==j else 0 for j in range(3)] for i in range(3)]
        rebuilt=matmul(matmul(axes,diagonal),transpose(axes))
        for i in range(3):
            for j in range(3): self.assertAlmostEqual(rebuilt[i][j],data['inertia_kg_cm2'][i][j])

    def test_nonphysical_inertia_rejected(self):
        for diag in ([0,1,1],[-1,1,1],[1,1,3]):
            with self.assertRaises(ValueError):
                principal_inertia([[diag[i] if i==j else 0 for j in range(3)] for i in range(3)])

    def test_complete_model_and_joint_units(self):
        d=description()
        self.assertEqual(len(d['bodies']),9)
        self.assertEqual(len(d['joints']),8)
        self.assertEqual(list(d['fixed_frames']),['grasp_center'])
        left=next(x for x in d['joints'] if x['name']=='gripper_left_joint')
        right=next(x for x in d['joints'] if x['name']=='gripper_right_joint')
        self.assertAlmostEqual(left['limits']['upper'],5.24125)
        self.assertEqual(left['limits']['effort'],2000)
        self.assertEqual(left['limits']['velocity'],10)
        self.assertEqual(right['mimic']['joint'],left['name'])
        self.assertAlmostEqual(math.dist(left['axis'],right['axis']),2)
        first=d['joints'][0]
        self.assertEqual(first['axis'],[0,0,1]) # Axial, not polar reflection.
        self.assertEqual(first['limits']['effort'],100000)
        self.assertAlmostEqual(first['damping'],10)
        self.assertAlmostEqual(first['friction'],1450)
        for name in ('gripper_left','gripper_right'):
            self.assertEqual(d['bodies'][name]['collision_parts'],d['bodies'][name]['visual_parts']+1)
        # Explicit sum from the source masses, including the fixed base.
        self.assertAlmostEqual(d['total_mass_kg'],3.871549999)

    def test_collision_export_preserves_hidden_jaw_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            visual=prepare(Path(tmp)/'visual')
            collision=prepare(Path(tmp)/'collision',geometry='collision')
            self.assertEqual(set(visual),set(collision))
            for name in visual:
                if name.startswith('gripper_'):
                    self.assertGreater(collision[name],visual[name])
                else:
                    self.assertEqual(collision[name],visual[name])
            for name in collision:
                text=(Path(tmp)/'collision'/(name+'.obj')).read_text()
                self.assertEqual(sum(x.startswith('f ') for x in text.splitlines()),collision[name])

if __name__=='__main__': unittest.main()
