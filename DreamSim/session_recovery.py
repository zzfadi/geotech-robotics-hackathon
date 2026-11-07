"""Continue a gateway-replaced Dream session without replaying a discarded action.

DreamClient documents that DreamSessionExpired adopts the replacement session
but returns no action. Only that exception is retried, once per control cycle.
"""

def request_action(get_action, refresh, cancelled, report, expired_type):
    for attempt in range(2):
        if cancelled():raise RuntimeError('Stopped before Dream inference')
        packet=refresh()
        if cancelled():raise RuntimeError('Stopped after refreshing Unreal feedback')
        try:
            action=get_action()
        except expired_type:
            if cancelled():raise RuntimeError('Stopped after Dream session expiry') from None
            if attempt:raise
            report('Dream renewed its inference session; retrying once from fresh Unreal feedback')
            continue
        if cancelled():raise RuntimeError('Stopped by user; discarded pending model response')
        return action,packet
    raise AssertionError('Unreachable session retry state')
