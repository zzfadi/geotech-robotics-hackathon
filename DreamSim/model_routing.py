"""Bounded model selection for lift trials; never treats height as task success."""
FLASH = 'dreamlabs/dream-1-beta-flash'
FULL = 'dreamlabs/dream-1-beta'

class ModelBudget:
    def __init__(self, full_limit=2, escalate_after=8):
        self.full_limit=full_limit;self.escalate_after=escalate_after
        self.full_calls=0;self.full_unavailable=False

    def choose(self, completed_cycles, lift_cm):
        # Missing telemetry is not evidence of a stall. Allow approach time first.
        if (lift_cm is not None and lift_cm < 2 and completed_cycles >= self.escalate_after
                and not self.full_unavailable and self.full_calls < self.full_limit):
            return FULL
        return FLASH

    def charge(self, checkpoint):
        if checkpoint == FULL:
            if self.full_calls >= self.full_limit:
                raise RuntimeError('Full-model call budget exhausted')
            self.full_calls += 1

    def capacity_fallback(self, checkpoint, error):
        if (checkpoint == FULL and getattr(error,'code',None) == 'DA-2000'
                and getattr(error,'reason',None) in ('pool_full','queue_timeout','max_serves','pool_warming')):
            self.full_unavailable=True
            return True
        return False


def red_cube_lift(status):
    props=status.get('props',{})
    matches=[p for name,p in props.items() if name.endswith('Block_Red')]
    if len(matches)!=1:return None
    p=matches[0]
    # A dropped block is stalled even if it briefly rose earlier.
    return p.get('final_z_cm',p['max_z_cm'])-p['initial_z_cm']
