#include "SCoPresenceCaptions.h"

#include "Styling/CoreStyle.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "CoPresenceCaptions"

namespace UE::CoPresenceAnnotate::Private
{
	// Dark and a little see-through, like captions in a video call, so the level shows around them
	const FLinearColor CaptionBackgroundColor(0.0f, 0.0f, 0.0f, 0.72f);
	const FLinearColor CaptionLabelColor(1.0f, 1.0f, 1.0f, 0.6f);
	constexpr float CaptionCornerRadius = 8.0f;
	constexpr int32 CaptionFontSize = 13;

	// Wide enough for a sentence, narrow enough to read at a glance
	constexpr float CaptionMaxWidth = 720.0f;

	// Clear of the viewport's bottom edge and the controls in its corners
	constexpr float CaptionBottomMargin = 48.0f;
	constexpr float CaptionLineGap = 4.0f;
}

SCoPresenceCaptions::SCoPresenceCaptions()
	: BackgroundBrush(UE::CoPresenceAnnotate::Private::CaptionBackgroundColor, UE::CoPresenceAnnotate::Private::CaptionCornerRadius)
{
}

void SCoPresenceCaptions::Construct(const FArguments& InArgs)
{
	using namespace UE::CoPresenceAnnotate::Private;

	SetVisibility(EVisibility::HitTestInvisible);
	const FSlateFontInfo LabelFont = FCoreStyle::GetDefaultFontStyle("Bold", CaptionFontSize);
	const FSlateFontInfo WordsFont = FCoreStyle::GetDefaultFontStyle("Regular", CaptionFontSize);

	ChildSlot
	.HAlign(HAlign_Center)
	.VAlign(VAlign_Bottom)
	.Padding(FMargin(16.0f, 0.0f, 16.0f, CaptionBottomMargin))
	[
		SNew(SBox)
		.MaxDesiredWidth(CaptionMaxWidth)
		[
			SNew(SBorder)
			.BorderImage(&BackgroundBrush)
			.Padding(FMargin(14.0f, 8.0f))
			[
				SNew(SVerticalBox)
				+SVerticalBox::Slot() // What the person says
				.AutoHeight()
				[
					SAssignNew(YourLine, SHorizontalBox)
					+SHorizontalBox::Slot() // Who
					.AutoWidth()
					.Padding(FMargin(0.0f, 0.0f, 8.0f, 0.0f))
					[
						SNew(STextBlock)
						.Text(LOCTEXT("You", "You"))
						.Font(LabelFont)
						.ColorAndOpacity(CaptionLabelColor)
					]
					+SHorizontalBox::Slot() // Their words
					.FillWidth(1.0f)
					[
						SAssignNew(YourText, STextBlock)
						.Font(WordsFont)
						.ColorAndOpacity(FLinearColor::White)
						.AutoWrapText(true)
					]
				]
				+SVerticalBox::Slot() // What the agent says back
				.AutoHeight()
				.Expose(AgentSlot)
				[
					SAssignNew(AgentLine, SHorizontalBox)
					+SHorizontalBox::Slot() // The agent's name, in its color
					.AutoWidth()
					.Padding(FMargin(0.0f, 0.0f, 8.0f, 0.0f))
					[
						SAssignNew(AgentLabel, STextBlock)
						.Font(LabelFont)
					]
					+SHorizontalBox::Slot() // Its words
					.FillWidth(1.0f)
					[
						SAssignNew(AgentText, STextBlock)
						.Font(WordsFont)
						.ColorAndOpacity(FLinearColor::White)
						.AutoWrapText(true)
					]
				]
			]
		]
	];
}

void SCoPresenceCaptions::SetLines(const FString& YourWords, const FString& AgentName, const FString& AgentWords, const FLinearColor& AgentColor)
{
	using namespace UE::CoPresenceAnnotate::Private;

	YourText->SetText(FText::FromString(YourWords));
	YourLine->SetVisibility(YourWords.IsEmpty() ? EVisibility::Collapsed : EVisibility::HitTestInvisible);
	AgentLabel->SetText(FText::FromString(AgentName));
	AgentLabel->SetColorAndOpacity(AgentColor);
	AgentText->SetText(FText::FromString(AgentWords));
	AgentLine->SetVisibility(AgentWords.IsEmpty() ? EVisibility::Collapsed : EVisibility::HitTestInvisible);
	AgentSlot->SetPadding(FMargin(0.0f, YourWords.IsEmpty() ? 0.0f : CaptionLineGap, 0.0f, 0.0f));
}

#undef LOCTEXT_NAMESPACE
