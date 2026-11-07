import json

import unreal

# Builds the CoPresence editor tabs: Editor Utility Widgets whose only content is a vertical
# box named Root. copresence.agents_hud (the Agents widget), copresence.agents_panel
# (Agent Chat) and copresence.fab_browser (an agent's Fab Browser) fill them in from Python
# when a tab opens, so the whole UI lives in code. Tabs that exist are kept; delete a tab's
# .uasset to rebuild it.
# Needs the UMG toolset, which projects don't enable by default:
#
#   UnrealEditor-Cmd AgentCoPresence.uproject -run=pythonscript
#       -script=Plugins/CoPresence/Scripts/create_agents_panel.py -EnablePlugins=UMGToolSet
PANEL_FOLDER = "/CoPresence"
# Asset name and tab title
PANELS = {"EUW_AgentStatus": "Agents", "EUW_AgentChat": "Chat", "EUW_FabBrowser": "Fab Browser"}
UMG_TOOLSET = "UMGToolSet.UMGToolSet"


def call(tool: str, arguments: dict):
    outcome = unreal.ToolsetRegistry.execute_tool(UMG_TOOLSET, tool, json.dumps(arguments))
    if outcome.error:
        raise RuntimeError(f"{tool} failed: {outcome.error}")
    decoded = json.loads(outcome.value) if outcome.value else {}
    return decoded.get("returnValue", decoded) if isinstance(decoded, dict) else decoded


def build_panel(name: str, title: str) -> None:
    path = f"{PANEL_FOLDER}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        raise RuntimeError(f"{path} exists; remove Plugins/CoPresence/Content/{name}.uasset and rerun")

    blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, PANEL_FOLDER, unreal.EditorUtilityWidgetBlueprint, unreal.EditorUtilityWidgetBlueprintFactory())
    reference = {"refPath": blueprint.get_path_name()}

    # Start from an empty tree whatever the factory put in it
    tree = call("GetWidgets", {"widgetBlueprint": reference})
    for widget in tree.get("widgets", []) if isinstance(tree, dict) else []:
        if not widget.get("parent"):
            call("RemoveWidget", {"widgetBlueprint": reference, "widget": widget["widget"]})

    root = call("AddWidget", {"widgetBlueprint": reference, "widgetClass": {"refPath": "/Script/UMG.VerticalBox"},
                              "widgetDisplayName": "Root"})
    call("ToggleWidgetAsVariable", {"widgetBlueprint": reference, "widget": root["widget"], "bIsVariable": True})

    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    defaults = unreal.get_default_object(blueprint.generated_class())
    defaults.set_editor_property("tab_display_name", unreal.Text(title))
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint)


unreal.AssetRegistryHelpers.get_asset_registry().scan_paths_synchronous([PANEL_FOLDER], True)
for panel_name, panel_title in PANELS.items():
    if not unreal.EditorAssetLibrary.does_asset_exist(f"{PANEL_FOLDER}/{panel_name}"):
        build_panel(panel_name, panel_title)
