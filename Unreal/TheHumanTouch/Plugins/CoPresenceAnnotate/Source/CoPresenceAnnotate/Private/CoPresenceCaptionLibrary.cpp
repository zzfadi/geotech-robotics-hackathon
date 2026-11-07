#include "CoPresenceCaptionLibrary.h"

#include "CoPresenceAnnotateModule.h"

void UCoPresenceCaptionLibrary::SetCaptions(const FString& YourWords, const FString& AgentName, const FString& AgentWords, FLinearColor AgentColor)
{
	if (!FCoPresenceAnnotateModule::IsAvailable())
	{
		return;
	}
	if (FCoPresenceCaptions* Captions = FCoPresenceAnnotateModule::Get().GetCaptions())
	{
		Captions->Set(YourWords, AgentName, AgentWords, AgentColor);
	}
}
