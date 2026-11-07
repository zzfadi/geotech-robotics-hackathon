#pragma once

#include "CoreMinimal.h"
#include "Framework/Commands/Commands.h"

/** The Annotate command, so its shortcut can be changed under Editor Preferences > Keyboard Shortcuts. */
class FCoPresenceAnnotateCommands : public TCommands<FCoPresenceAnnotateCommands>
{
public:
	FCoPresenceAnnotateCommands();

	//~ Begin TCommands
	virtual void RegisterCommands() override;
	//~ End TCommands

	TSharedPtr<FUICommandInfo> ToggleAnnotating;
};
