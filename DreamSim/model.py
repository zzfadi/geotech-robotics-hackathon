"""Dream IK model derived from the exact Maker Arm used by Unreal.

Simulation only: CAD tool frame is authoritative here, not a calibration claim
about hardware. Never use the SDK's built-in `makers` joint convention directly.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
MODEL=ROOT/'Unreal/TheHumanTouch/SourceAssets/MakerArm/robot.urdf'
REST=(0.,-1.2,.81,-.1,0.,0.)


def prepare(destination,tcp_back=0.):
    if not math.isfinite(tcp_back) or not 0<=tcp_back<=.04:raise ValueError("Simulation TCP offset must be 0..4 cm")
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(MODEL.read_bytes()).hexdigest()
    provenance=json.loads((MODEL.parent/'PROVENANCE.json').read_text())
    if digest!=provenance['sha256']['robot.urdf']:raise ValueError('Pinned URDF changed')
    source=ET.parse(MODEL).getroot()
    derived=ET.Element('robot',name='maker_arm_unreal_simulation')
    ET.SubElement(derived,'link',name='dream_base')
    for link in source.findall('link'):ET.SubElement(derived,'link',name=link.get('name'))
    fixed=ET.SubElement(derived,'joint',name='dream_mount',type='fixed')
    ET.SubElement(fixed,'parent',link='dream_base');ET.SubElement(fixed,'child',link='base_link')
    ET.SubElement(fixed,'origin',xyz='0 0 0',rpy=f'{math.pi/2} 0 {math.pi}')
    for joint in source.findall('joint'):derived.append(copy.deepcopy(joint))
    joints=source.findall("joint[@type='revolute']")
    if len(joints)!=6:raise ValueError('Expected six Maker Arm joints')
    for joint,value in zip(joints,REST):
        limit=joint.find('limit')
        if not float(limit.get('lower'))<=value<=float(limit.get('upper')):raise ValueError('Rest outside CAD limits')
    grasp=source.find("joint[@name='grasp_center_joint']")
    if grasp.find('parent').get('link')!='link_007':raise ValueError('Unexpected grasp parent')
    if grasp.find('origin').get('rpy')!='0 0 0':raise ValueError('Review changed grasp axes')
    jaw=source.find("joint[@name='gripper_left_joint']/axis").get('xyz')
    config={'urdf':'maker_arm_sim.urdf','base':'dream_base','flange':'link_007',
            'tcp':[float(x) for x in grasp.find('origin').get('xyz').split()],
            'approach':[0.,0.,-1.],'jaw':[float(x) for x in jaw.split()], 'rest':list(REST)}
    config['tcp'][2]+=tcp_back
    ET.indent(derived);ET.ElementTree(derived).write(destination/config['urdf'],encoding='utf-8',xml_declaration=True)
    (destination/'maker_arm_sim.json').write_text(json.dumps(config,indent=2)+'\n')
    (destination/'provenance.json').write_text(json.dumps({'source_commit':provenance['commit'],'source_urdf_sha256':digest,
        'scope':'UE simulation only; exact CAD axes and limits; no hardware calibration',
        'simulation_tcp_back_m':tcp_back,'mount_rpy':[math.pi/2,0,math.pi],'joints':[j.get('name') for j in joints],
        'jaw_opening_m':.104825,'dream_builtin_makers_compatible':False},indent=2)+'\n')
    return destination/'maker_arm_sim.json'

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('destination');a=p.parse_args();print(prepare(a.destination))
