#include "CoPresenceCaptions.h"

#include "LevelEditor.h"
#include "Modules/ModuleManager.h"
#include "SCoPresenceCaptions.h"
#include "SLevelViewport.h"

FCoPresenceCaptions::~FCoPresenceCaptions()
{
	Detach();
}

void FCoPresenceCaptions::Set(const FString& YourWords, const FString& AgentName, const FString& AgentWords, const FLinearColor& AgentColor)
{
	if (YourWords.IsEmpty() && AgentWords.IsEmpty())
	{
		Detach();
		return;
	}

	FLevelEditorModule* LevelEditor = FModuleManager::GetModulePtr<FLevelEditorModule>(TEXT("LevelEditor"));
	const TSharedPtr<SLevelViewport> Active = LevelEditor ? LevelEditor->GetFirstActiveLevelViewport() : nullptr;
	if (!Active.IsValid())
	{
		Detach();
		return;
	}

	if (!Widget.IsValid())
	{
		Widget = SNew(SCoPresenceCaptions);
	}

	// The person may have gone to another viewport, or closed the one the captions were in
	if (Viewport.Pin() != Active)
	{
		Detach();
		Active->AddOverlayWidget(Widget.ToSharedRef());
		Viewport = Active;
	}
	Widget->SetLines(YourWords, AgentName, AgentWords, AgentColor);
}

void FCoPresenceCaptions::Detach()
{
	const TSharedPtr<SLevelViewport> Attached = Viewport.Pin();
	if (Attached.IsValid() && Widget.IsValid())
	{
		Attached->RemoveOverlayWidget(Widget.ToSharedRef());
	}
	Viewport.Reset();
}
