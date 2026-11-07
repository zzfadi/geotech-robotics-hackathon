#include "CoPresenceAnnotateCommands.h"

#include "Styling/AppStyle.h"

#define LOCTEXT_NAMESPACE "CoPresenceAnnotate"

FCoPresenceAnnotateCommands::FCoPresenceAnnotateCommands()
	: TCommands<FCoPresenceAnnotateCommands>(
		TEXT("CoPresenceAnnotate"),
		LOCTEXT("ContextDescription", "CoPresence Annotate"),
		NAME_None,
		FAppStyle::GetAppStyleSetName())
{
}

void FCoPresenceAnnotateCommands::RegisterCommands()
{
	// Ctrl+Space opens the Content Drawer and Ctrl+Shift+Space steps the viewport and the Rewind
	// Debugger, so Windows gets Ctrl+Alt+Space. On a Mac, Unreal's Command modifier is the
	// Control key, and Control+Shift+Space is free there, while Command+Option+Space opens a Finder search
#if PLATFORM_MAC
	const FInputChord DefaultChord(EModifierKey::Command | EModifierKey::Shift, EKeys::SpaceBar);
#else
	const FInputChord DefaultChord(EModifierKey::Control | EModifierKey::Alt, EKeys::SpaceBar);
#endif
	UI_COMMAND(ToggleAnnotating, "Annotate", "Draw on the editor to point the agents at things, then tell them what you mean",
		EUserInterfaceActionType::ToggleButton, DefaultChord);
}

#undef LOCTEXT_NAMESPACE
