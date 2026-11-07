"""One Dream inference against captured UE observations. Does not actuate robots."""
import argparse
import base64
import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image
from dream import DreamClient
from model import prepare


def configure(client,arm):
    body={'state':{'right_arm':[0,6],'right_grip':[6,7],'left_arm':[7,13],'left_grip':[13,14]},
          'action':{'right_arm':[0,6],'right_grip':[6,7],'left_arm':[7,13],'left_grip':[13,14]},
          'video':{role:'observation.images.'+role for role in ('overhead','wrist_l','wrist_r')},'head_camera':'overhead'}
    body['state'].update({'right_eef_xyz':[14,17],'right_eef_rot6':[17,23],'left_eef_xyz':[23,26],'left_eef_rot6':[26,32]})
    client.policy({'embodiment':body,'checkpoint':'dreamlabs/dream-1-beta-flash','ik':{'arm':str(arm.resolve()),'head':'eef'},'timeout_ms':30000})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--overhead',type=Path,required=True)
    parser.add_argument('--left',type=Path,required=True)
    parser.add_argument('--right',type=Path,required=True)
    parser.add_argument('--prompt',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    state=json.loads(args.state.read_text())
    frames={}
    for key,path in [('overhead',args.overhead),('wrist_l',args.left),('wrist_r',args.right)]:
        buffer=io.BytesIO()
        Image.open(path).convert('RGB').resize((320,176),Image.Resampling.LANCZOS).save(buffer,format='JPEG',quality=90)
        frames['video.'+key]=base64.b64encode(buffer.getvalue()).decode('ascii')
    observation={'prompt':args.prompt}
    for side in ('left','right'):
        q=np.asarray(state['q_'+side],dtype=float)
        gap=float(state['gap_'+side+'_m'])
        if q.shape!=(6,) or not np.isfinite(q).all():raise ValueError('Invalid measured joints')
        if not 0<=gap<=.104825:raise ValueError('Invalid measured jaw gap')
        observation['state.'+side+'_arm']=q
        observation['state.'+side+'_grip']=[gap/.104825]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    arm=prepare(args.output.parent/'model')
    client=DreamClient()
    try:
        configure(client,arm)
        print('Policy configured; warming custom CAD IK',flush=True)
        if not client.wait_for_ik(180):raise TimeoutError('Custom IK warmup exceeded 180 seconds')
        client.set_observation(frames,observation)
        started=time.monotonic()
        action=client.get_action()
        report={'scope':'inference only; no UE motion applied','elapsed_seconds':time.monotonic()-started,
                'prompt':args.prompt,'state':state,'action':action}
        args.output.write_text(json.dumps(report,default=lambda x:x.tolist() if hasattr(x,'tolist') else str(x),indent=2)+'\n')
        print(json.dumps({'output':str(args.output),'elapsed_seconds':report['elapsed_seconds'],
                          'chunk_shapes':{key:list(np.asarray(action[key]).shape) for key in ('q_l_chunk','q_r_chunk') if key in action}}),flush=True)
    finally:client.close()

if __name__=='__main__':main()
