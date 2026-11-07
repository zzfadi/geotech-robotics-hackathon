"""Validated host/brain/Git roles shared by the session launcher and services."""
from dataclasses import asdict, dataclass

HOSTS = ('mac', 'luna2')
BRAINS = ('codex', 'claude')

@dataclass(frozen=True)
class LaunchPlan:
    scenario: str
    hosts: tuple[str, ...]
    mode: str
    server_host: str | None
    call_host: str
    writer: str
    reader: str | None
    brains: dict[str, str]

    def as_dict(self):
        return asdict(self)


def plan(scenario='both', *, writer=None, call_host=None,
         mac_brain='codex', luna2_brain='claude'):
    if scenario not in (*HOSTS, 'both'):
        raise ValueError('Choose mac, luna2, or both')
    hosts = HOSTS if scenario == 'both' else (scenario,)
    writer = writer or ('luna2' if 'luna2' in hosts else 'mac')
    call_host = call_host or writer
    if writer not in hosts or call_host not in hosts:
        raise ValueError('The writer and call host must be active hosts')
    if mac_brain not in BRAINS or luna2_brain not in BRAINS:
        raise ValueError('Brain must be claude or codex')
    return LaunchPlan(scenario, hosts, 'multiuser' if len(hosts) == 2 else 'solo',
                      'luna2' if len(hosts) == 2 else None, call_host, writer,
                      next((h for h in hosts if h != writer), None),
                      {h: {'mac': mac_brain, 'luna2': luna2_brain}[h] for h in hosts})
