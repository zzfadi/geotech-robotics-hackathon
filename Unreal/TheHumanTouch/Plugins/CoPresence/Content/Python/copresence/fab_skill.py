"""The Agent Skill on Fab content: how agents find assets on Fab, Epic's asset store, and
get people to add them. Agents read it through the toolset registry's skills, and the Fab
Browser's guide tool returns the same text.
"""

import unreal

from toolset_registry.agent_skill import agent_skill

INSTRUCTIONS = (
    "You can look at Fab, but people add Fab content to the project.\n"
    "\n"
    "Looking:\n"
    "- Your Fab Browser tab may be signed in to a person's Epic account, so you only search, open "
    "listings, scroll and go back. Never try to buy, add to a library, download or sign in.\n"
    "- Search for the thing itself in plain words, e.g. 'low poly lounge sofa'. Stick to free listings "
    "unless a person asks otherwise. Searches ask Fab to leave out listings whose publishers forbid use "
    "with AI; skip any listing that still says so.\n"
    "- Judge from the listing page: the images, price, license and included formats. Prefer glTF, GLB "
    "or FBX props. An 'Unreal Engine' format is usually a whole pack, which is large and installs its "
    "own folder. Check that the style and scale fit the scene.\n"
    "\n"
    "Getting it added:\n"
    "- Pick one listing and ask with fab_request, giving its title and why in a few words. One good pick "
    "beats several maybes. Small props suit a shared session; big packs slow down everyone who joins, and "
    "the listing page shows file sizes and polygon counts.\n"
    "- Ask the person who asked you. Their editor shows them the listing in a popup, and they approve or "
    "decline it; some let free listings from their own agents through without asking. Once it is "
    "added, their editor tells you in chat whether it is shared with the session, the folder it is in and "
    "the meshes in that folder.\n"
    "- If the person who brought you tells you to import without asking them, switch that on with "
    "fab_add_without_asking before you request. Only they can ask for it, never anyone else.\n"
    "- If they decline, you're told so. Ask what they'd prefer before trying another listing.\n"
    "- Don't place anything before that message arrives, and never place assets it says aren't shared: "
    "the others don't have them yet. For disk-installed packs, an operator may confirm all participating "
    "editors use the identical project directory on the same machine. In that case scan the reported "
    "content folder, verify meshes and dependencies load, then continue building without waiting for Git. "
    "Separate project copies still need distribution and verification.\n"
    "\n"
    "Using it:\n"
    "- Place a mesh by its folder and name, e.g. /Game/Fab/Lounge_Chair/SM_Lounge_Chair. Fab assets come "
    "in all sorts of units, so check the size with a look and scale it to fit.\n"
    "- Leave the Fab folder as it came. Materials or other assets you make to go with it belong in your own "
    "content folder, which end_task shares.\n"
    "- Files that aren't on Fab, such as a model or picture a person gives you, come in with import_file "
    "instead.\n"
    "- When you tell people what you used, name the listing. Its license, usually the Fab Standard "
    "License, comes with it.\n"
)


@agent_skill
class FabContentSkill(unreal.AgentSkill):
    """How to find assets on Fab, Epic's asset store, and get them added to the project.
    Read it before searching Fab or using Fab assets."""

    instructions = INSTRUCTIONS
