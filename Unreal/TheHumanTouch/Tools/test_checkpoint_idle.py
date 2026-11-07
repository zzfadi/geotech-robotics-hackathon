import io
import json
import unittest
from unittest.mock import patch
import checkpoint

class HubIdleTests(unittest.TestCase):
    def check(self, agents):
        with patch('checkpoint.urllib.request.urlopen',return_value=io.BytesIO(json.dumps({'agents':agents}).encode())):
            checkpoint.hub_idle()

    def test_completed_title_is_history(self):
        self.check({'Mac':{'state':'done','task':'Previous completed task'},'Luna2':{'state':'idle','task':''}})

    def test_active_or_unknown_state_defers(self):
        for state in ('working','thinking','listening','new-state',None):
            with self.subTest(state=state),self.assertRaises(RuntimeError):
                self.check({'Mac':{'state':state,'task':''}})

    def test_editor_active_task_still_defers(self):
        info={'agent':checkpoint.POLICY['host_agent'],'session':checkpoint.POLICY['session'],
              'task':'live-task','animation':False,'content':[]}
        with self.assertRaises(RuntimeError): checkpoint.ready(info)

if __name__=='__main__': unittest.main()
