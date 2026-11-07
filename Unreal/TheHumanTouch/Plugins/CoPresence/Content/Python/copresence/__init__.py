from toolset_registry.registration import Registration

# Importing it registers the Fab content Agent Skill
from copresence import fab_skill  # noqa: F401
from copresence.fab_browser import FabBrowserTools
from copresence.toolset import CoPresenceTools

_registration = Registration([CoPresenceTools, FabBrowserTools])
