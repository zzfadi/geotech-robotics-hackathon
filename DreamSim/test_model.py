"""Regression checks for custom Dream/UE joint and tool frame agreement."""
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from model import MODEL, ROOT, prepare
sys.path.insert(0,str(ROOT/'Unreal/TheHumanTouch/Tools'))
from maker_arm_model import mounted_frames
from yam_kinematics import identity,mul,origin,rotation,numbers

class ModelTest(unittest.TestCase):
    def test_source_joints_preserved_and_frames_match(self):
        with tempfile.TemporaryDirectory() as temp:
            config=json.loads(prepare(temp).read_text())
            derived=ET.parse(Path(temp)/config['urdf']).getroot()
            source=ET.parse(MODEL).getroot()
            for original in source.findall('joint'):
                copied=derived.find("joint[@name='%s']"%original.get('name'))
                for a,b in zip(original.iter(),copied.iter()):
                    self.assertEqual((a.tag,a.attrib),(b.tag,b.attrib))
            for q in ([0.]*6,[0.,-1.2,.81,-.1,0.,0.],[.3,-.8,1.1,.2,.4,-.5]):
                transform=identity();index=0
                for joint in derived.findall('joint'):
                    if joint.get('name')=='grasp_center_joint':continue
                    if joint.find('child').get('link') not in ('base_link','link_002','link_003','link_004','link_005','link_006','link_007'):continue
                    transform=mul(transform,origin(joint.find('origin')))
                    if joint.get('type')=='revolute':
                        transform=mul(transform,rotation(numbers(joint.find('axis').get('xyz')),q[index]));index+=1
                tcp=identity()
                for i,v in enumerate(config['tcp']):tcp[i][3]=v
                actual=mul(transform,tcp)
                expected=mounted_frames(q,yaw=math.pi)['grasp_center']
                for i in range(4):
                    for j in range(4):self.assertAlmostEqual(actual[i][j],expected[i][j],places=12)
            self.assertEqual(config['approach'],[0,0,-1])
            for actual,expected in zip(config['jaw'],[0,1,0]):self.assertAlmostEqual(actual,expected,places=6)

    def test_opt_in_contact_center_preserves_urdf(self):
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            original=json.loads(prepare(a).read_text());contact=json.loads(prepare(b,tcp_back=.04).read_text())
            self.assertEqual((Path(a)/original['urdf']).read_bytes(),(Path(b)/contact['urdf']).read_bytes())
            self.assertAlmostEqual(contact['tcp'][2]-original['tcp'][2],.04)
            self.assertEqual(contact['tcp'][:2],original['tcp'][:2])
        with self.assertRaises(ValueError):prepare('/tmp/unused',tcp_back=.05)

if __name__=='__main__':unittest.main()
