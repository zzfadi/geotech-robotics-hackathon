#pragma once

#include "CoreMinimal.h"

class SCoPresenceCaptions;
class SLevelViewport;

/** Keeps a call's captions in the level viewport the person works in, and out of it while nobody talks. */
class FCoPresenceCaptions
{
public:
	~FCoPresenceCaptions();

	/** Shows each line that has words; with none on either, the captions go away. */
	void Set(const FString& YourWords, const FString& AgentName, const FString& AgentWords, const FLinearColor& AgentColor);

private:
	void Detach();

	TSharedPtr<SCoPresenceCaptions> Widget;
	TWeakPtr<SLevelViewport> Viewport;
};
