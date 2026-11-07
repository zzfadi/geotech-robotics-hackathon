#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"

#include "CoPresenceCaptionLibrary.generated.h"

/**
 * Live captions of a person's voice call with an agent, along the bottom of the level viewport:
 * what they say, as the agent's voice hears it, and what the agent says back. The CoPresence
 * Python feeds them from the voice helper's status while a call is on.
 */
UCLASS()
class COPRESENCEANNOTATE_API UCoPresenceCaptionLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Shows each line that has words; with none on either, the captions go away.
	 * @param AgentColor The agent's color, for its name
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Captions")
	static void SetCaptions(const FString& YourWords, const FString& AgentName, const FString& AgentWords, FLinearColor AgentColor);
};
