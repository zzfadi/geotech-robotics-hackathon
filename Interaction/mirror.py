"""HTTPS measured-feedback consumer and atomic local Unreal mailbox. No motor output."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.parse
import urllib.request
from .feedback_pose import measured_feedback_pose
from .telemetry import NoRedirect


class MirrorConsumer:
    def __init__(self, base_url, *, session_id, host, calibration, clock=time.time):
        url=urllib.parse.urlsplit(base_url)
        if (url.scheme!='https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ('','/')):
            raise ValueError('HTTPS origin required')
        if not isinstance(session_id,str) or not re.fullmatch('[0-9a-f]{32}',session_id):
            raise ValueError('Pin the explicitly registered controller session')
        if not isinstance(host,str) or not host.strip():raise ValueError('Pin the selected SDK host')
        if not isinstance(calibration,dict) or calibration.get('validated') is not True:
            raise ValueError('Validated calibration required; no default mapping')
        self.url=base_url.rstrip('/')+'/api/interaction/status'
        self.session_id,self.host,self.calibration=session_id,host,calibration
        self.clock=clock;self.opener=urllib.request.build_opener(NoRedirect);self.last_stamp=None

    def convert(self, status):
        if not isinstance(status,dict) or status.get('table')!=10:
            raise ValueError('Expected Table 10 status')
        if status.get('session_id')!=self.session_id or status.get('host')!=self.host:
            raise ValueError('Controller session or host changed; re-registration required')
        if status.get('controller_online') is not True or not isinstance(status.get('state'),dict):
            raise ValueError('SDK controller is disconnected or feedback is stale')
        if status.get('side')!=self.calibration.get('side'):
            raise ValueError('Controller arm does not match calibration')
        pose=measured_feedback_pose(status['state'],self.calibration,self.clock())
        stamp=pose['feedback_unix']
        if self.last_stamp is not None and stamp<self.last_stamp:
            raise ValueError('Out-of-order measured feedback')
        self.last_stamp=stamp
        # Read-only measured motion remains visible even if command delivery stops.
        return pose

    def poll_once(self):
        try:
            with self.opener.open(self.url,timeout=.2) as response:
                raw=response.read(65537)
                if len(raw)>65536:raise ValueError('Oversized status response')
                status=json.loads(raw)
            packet=self.convert(status)
            return {'status':'fresh','session_id':self.session_id,'received_unix':self.clock(),
                    'packet':packet,'reason':None}
        except (OSError,ValueError,TypeError,KeyError):
            # No stale packet retained and no exception body/credential leakage.
            return {'status':'unavailable','session_id':self.session_id,'received_unix':self.clock(),
                    'packet':None,'reason':'No fresh registered and calibrated SDK feedback'}


def write_mailbox(path, envelope):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w') as stream:
            json.dump(envelope,stream,allow_nan=False)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='https://luna2.tail6864ac.ts.net')
    parser.add_argument('--session',required=True)
    parser.add_argument('--host',required=True)
    parser.add_argument('--calibration',type=Path,required=True)
    parser.add_argument('--mailbox',type=Path,required=True)
    args=parser.parse_args()
    consumer=MirrorConsumer(args.url,session_id=args.session,host=args.host,
                            calibration=json.loads(args.calibration.read_text()))
    try:
        while True:
            started=time.monotonic()
            write_mailbox(args.mailbox,consumer.poll_once())
            time.sleep(max(0,.1-(time.monotonic()-started)))
    except KeyboardInterrupt:
        pass
    finally:
        write_mailbox(args.mailbox,{'status':'unavailable','packet':None,
            'session_id':args.session,'received_unix':time.time(),'reason':'Mirror transport stopped'})


if __name__=='__main__':main()
