import copresence_call

# Agents' editors get this plugin from the launch scripts, so their tools include the call camera
copresence_call._registration.register()

from copresence import agent_editor
from copresence_call import roster, spectator_camera

# Agents' editors and the spectator tell the session hub who is in the session, for the session
# dashboard; the spectator's editor also films the session for it (Tools/copresence_spectator.py)
if not agent_editor.is_commandlet():
    if spectator_camera.is_spectator():
        roster.start("spectator")
        spectator_camera.install()
    elif agent_editor.is_agent_editor():
        roster.start("agent")
