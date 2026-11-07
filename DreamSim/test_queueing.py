import unittest
from queueing import wait_for_editor

class QueueTest(unittest.TestCase):
    def test_busy_then_success(self):
        calls=[];messages=[]
        def begin():
            calls.append(1)
            if len(calls)<3:raise RuntimeError('A task is already active. Call end_task first.')
            return 'started'
        self.assertEqual(wait_for_editor(begin,lambda:False,messages.append,sleep=lambda _:None),'started')
        self.assertEqual(len(messages),1)
    def test_cancel_never_begins(self):
        with self.assertRaisesRegex(RuntimeError,'Stopped'):
            wait_for_editor(lambda:self.fail('must not begin'),lambda:True,lambda _:None)
    def test_other_errors_not_retried(self):
        def begin():raise RuntimeError('Connection refused')
        with self.assertRaisesRegex(RuntimeError,'Connection refused'):
            wait_for_editor(begin,lambda:False,lambda _:None)

if __name__=='__main__':unittest.main()
