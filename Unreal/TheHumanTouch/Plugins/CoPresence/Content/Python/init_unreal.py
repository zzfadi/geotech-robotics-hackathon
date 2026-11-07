"""Project-neutral CoPresence startup for The Human Touch."""
import unreal
import copresence
from copresence import agent_editor, agents_hud, agents_panel, ghost_labels, ui_widgets

copresence._registration.register()
if not agent_editor.is_commandlet():
    # Both the person and the agent use this editor. Keep human controls available.
    from copresence import mcp_startup
    mcp_startup.install()
    ghost_labels.start()
    agents_hud.start()
    agents_panel.start()
    ui_widgets.after(3.0, lambda: ui_widgets.open_docked([agents_hud.tab, agents_panel.tab]))
