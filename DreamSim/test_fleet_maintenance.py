import unittest
from fleet_worker import poll_fleet_work

class MaintenanceTests(unittest.TestCase):
    def test_blocks_submissions_and_never_claims_existing_work(self):
        class Server:
            def __init__(self):self.calls=[]
            def api(self,path,data=None):
                self.calls.append((path,data))
                if path=='/api/fleet':return {'preview_enabled':True,'command':{'status':'queued'},'reset':{'status':'queued'}}
                if path.endswith('/next'):raise AssertionError('Must not claim queued work')
                return {'ok':True}
        server=Server();hello=[{'index':i,'ready':True} for i in range(9)]
        reply=poll_fleet_work(server,hello,True)
        self.assertIsNone(reply['command']);self.assertIsNone(reply['reset']);self.assertTrue(reply['preview_enabled'])
        advertised=server.calls[0][1];self.assertFalse(advertised['reset_supported'])
        self.assertTrue(all(not t['ready'] for t in advertised['tables']))
        self.assertTrue(all(t['ready'] for t in hello))
    def test_normal_poll_unchanged(self):
        class Server:
            def api(self,path,data=None):
                if data:self.payload=data;return {'ok':True}
                return {'command':{'id':'actual'},'reset':None}
        s=Server();r=poll_fleet_work(s,[{'index':0,'ready':True}])
        self.assertEqual(r['command']['id'],'actual');self.assertTrue(s.payload['reset_supported'])

if __name__=='__main__':unittest.main()
