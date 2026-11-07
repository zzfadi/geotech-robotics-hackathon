import unittest
from session_recovery import request_action

class Expired(Exception):pass

class RecoveryTest(unittest.TestCase):
    def test_expired_session_refreshes_then_returns_only_new_action(self):
        calls=[];packets=iter([{'sequence':1},{'sequence':2}]);reports=[]
        def action():
            calls.append('request')
            if len(calls)==1:raise Expired('adopted replacement')
            return {'q':'new response'}
        result,packet=request_action(action,lambda:next(packets),lambda:False,reports.append,Expired)
        self.assertEqual(result,{'q':'new response'});self.assertEqual(packet,{'sequence':2})
        self.assertEqual(len(calls),2);self.assertEqual(len(reports),1)
    def test_repeated_expiry_is_bounded(self):
        calls=[]
        def action():calls.append(1);raise Expired()
        with self.assertRaises(Expired):request_action(action,lambda:{},lambda:False,lambda _:None,Expired)
        self.assertEqual(len(calls),2)
    def test_other_errors_are_not_retried(self):
        calls=[]
        def action():calls.append(1);raise ValueError('bad trajectory')
        with self.assertRaises(ValueError):request_action(action,lambda:{},lambda:False,lambda _:None,Expired)
        self.assertEqual(len(calls),1)
    def test_stop_after_expiry_never_retries(self):
        stopped=False;calls=[]
        def action():
            nonlocal stopped
            calls.append(1);stopped=True;raise Expired()
        with self.assertRaisesRegex(RuntimeError,'Stopped'):request_action(action,lambda:{},lambda:stopped,lambda _:None,Expired)
        self.assertEqual(len(calls),1)
    def test_stop_during_refresh_prevents_request(self):
        stopped=False;calls=[]
        def refresh():
            nonlocal stopped
            stopped=True;return {}
        with self.assertRaisesRegex(RuntimeError,'Stopped'):request_action(lambda:calls.append(1),refresh,lambda:stopped,lambda _:None,Expired)
        self.assertEqual(calls,[])
    def test_stop_discards_successful_response(self):
        stopped=False
        def action():
            nonlocal stopped
            stopped=True;return {'q':'never apply'}
        with self.assertRaisesRegex(RuntimeError,'discarded'):request_action(action,lambda:{},lambda:stopped,lambda _:None,Expired)

if __name__=='__main__':unittest.main()
