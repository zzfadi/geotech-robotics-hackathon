#pragma once

#include "CoreMinimal.h"
#include "Brushes/SlateRoundedBoxBrush.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SCompoundWidget.h"

class STextBlock;

/**
 * Live captions of a voice call with an agent, along the bottom of the level viewport: what the
 * person says, as the agent's voice hears it, so they can tell their microphone works, and what
 * the agent says back. It takes no pointer input.
 */
class SCoPresenceCaptions : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SCoPresenceCaptions) {}
	SLATE_END_ARGS()

	SCoPresenceCaptions();

	void Construct(const FArguments& InArgs);

	/** Shows each line that has words and hides the other. */
	void SetLines(const FString& YourWords, const FString& AgentName, const FString& AgentWords, const FLinearColor& AgentColor);

private:
	FSlateRoundedBoxBrush BackgroundBrush;

	TSharedPtr<SHorizontalBox> YourLine;
	TSharedPtr<STextBlock> YourText;
	TSharedPtr<SHorizontalBox> AgentLine;
	TSharedPtr<STextBlock> AgentLabel;
	TSharedPtr<STextBlock> AgentText;

	/** The agent's line, a little apart from the person's while both show */
	SVerticalBox::FSlot* AgentSlot = nullptr;
};
