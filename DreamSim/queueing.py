"""Wait for an editor task slot without discarding the user's command."""
import time

def wait_for_editor(begin,cancelled,report,timeout=120,clock=time.monotonic,sleep=time.sleep):
    deadline=clock()+timeout;announced=False
    while True:
        if cancelled():raise RuntimeError('Stopped while waiting for the editor')
        try:return begin()
        except RuntimeError as error:
            if 'A task is already active' not in str(error):raise
            if not announced:report('Waiting for the current Unreal task to finish');announced=True
            if clock()>=deadline:raise TimeoutError('Unreal remained busy for two minutes; no motion was applied') from None
            sleep(.5)
