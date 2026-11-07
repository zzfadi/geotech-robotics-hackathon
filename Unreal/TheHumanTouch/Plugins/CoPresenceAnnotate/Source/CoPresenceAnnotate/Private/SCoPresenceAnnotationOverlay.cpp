#include "SCoPresenceAnnotationOverlay.h"

#include "CoPresenceAnnotateModule.h"
#include "Fonts/FontMeasure.h"
#include "Framework/Application/SlateApplication.h"
#include "Rendering/DrawElements.h"
#include "Styling/AppStyle.h"
#include "Styling/CoreStyle.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SCanvas.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "CoPresenceAnnotate"

namespace UE::CoPresenceAnnotate::Private
{
	// A warm red, like a recording light, so annotation mode is hard to miss
	const FLinearColor AccentColor(0.95f, 0.20f, 0.05f, 1.0f);
	const FLinearColor TintColor(0.95f, 0.20f, 0.05f, 0.08f);
	const FLinearColor StrokeShadowColor(0.0f, 0.0f, 0.0f, 0.45f);
	const FLinearColor LabelColor(0.008f, 0.008f, 0.008f, 0.88f);
	const FLinearColor NoteColor(0.012f, 0.012f, 0.012f, 0.97f);
	const FLinearColor MutedTextColor(1.0f, 1.0f, 1.0f, 0.75f);

	constexpr float FrameThickness = 4.0f;
	constexpr float StrokeThickness = 5.0f;
	constexpr float StrokeShadowThickness = 9.0f;
	constexpr float DotDiameter = 12.0f;
	constexpr double NoteWidth = 380.0;
	constexpr double NoteGap = 14.0;
	constexpr double EdgeMargin = 12.0;
	constexpr double LabelGap = 6.0;
	const FVector2D LabelPadding(7.0, 3.0);
}

SCoPresenceAnnotationOverlay::SCoPresenceAnnotationOverlay()
	: LabelBrush(UE::CoPresenceAnnotate::Private::LabelColor, 4.0f)
	, DotBrush(UE::CoPresenceAnnotate::Private::AccentColor, UE::CoPresenceAnnotate::Private::DotDiameter * 0.5f,
		UE::CoPresenceAnnotate::Private::StrokeShadowColor, 1.5f)
	, BannerBrush(UE::CoPresenceAnnotate::Private::AccentColor, 12.0f)
	, NoteBrush(UE::CoPresenceAnnotate::Private::NoteColor, 6.0f, UE::CoPresenceAnnotate::Private::AccentColor, 1.0f)
{
}

void SCoPresenceAnnotationOverlay::Construct(const FArguments& InArgs)
{
	using namespace UE::CoPresenceAnnotate::Private;

	// Children that want the pointer, like the note box, still get it
	SetVisibility(EVisibility::SelfHitTestInvisible);

	NoteBox = SNew(SBorder)
		.BorderImage(&NoteBrush)
		.Padding(FMargin(6.0f))
		[
			SAssignNew(NoteText, SEditableTextBox)
			.HintText_Lambda([]()
			{
				return FCoPresenceAnnotateModule::Get().GetNoteHint();
			})
			.ClearKeyboardFocusOnCommit(false)
			.OnTextCommitted(this, &SCoPresenceAnnotationOverlay::HandleNoteCommitted)
		];

	ChildSlot
	[
		SNew(SOverlay)
		+SOverlay::Slot() // What the mode is and how to leave it
		.HAlign(HAlign_Center)
		.VAlign(VAlign_Top)
		.Padding(FMargin(0.0f, 10.0f, 0.0f, 0.0f))
		[
			SNew(SBorder)
			.BorderImage(&BannerBrush)
			.Padding(FMargin(14.0f, 5.0f))
			.Visibility(this, &SCoPresenceAnnotationOverlay::GetBannerVisibility)
			[
				SNew(SHorizontalBox)
				+SHorizontalBox::Slot() // Mode
				.AutoWidth()
				.Padding(FMargin(0.0f, 0.0f, 10.0f, 0.0f))
				[
					SNew(STextBlock)
					.Text(LOCTEXT("BannerTitle", "Annotating"))
					.Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
					.ColorAndOpacity(FLinearColor::White)
				]
				+SHorizontalBox::Slot() // How to use it
				.AutoWidth()
				.VAlign(VAlign_Center)
				[
					SNew(STextBlock)
					.Text(LOCTEXT("BannerHint", "Draw on anything, then say what you mean."))
					.Font(FCoreStyle::GetDefaultFontStyle("Regular", 10))
					.ColorAndOpacity(MutedTextColor)
				]
				+SHorizontalBox::Slot() // Leaving, for people who started from the toolbar
				.AutoWidth()
				.Padding(FMargin(10.0f, 0.0f, 0.0f, 0.0f))
				[
					SAssignNew(StopButton, SButton)
					.ButtonStyle(FAppStyle::Get(), "SimpleButton")
					.ContentPadding(FMargin(6.0f, 0.0f))
					.ToolTipText(LOCTEXT("StopTooltip", "Stop annotating and drop the drawing (Esc)"))
					.OnClicked_Lambda([]()
					{
						FCoPresenceAnnotateModule::Get().SetAnnotating(false);
						return FReply::Handled();
					})
					[
						SNew(STextBlock)
						.Text(LOCTEXT("Stop", "Stop"))
						.Font(FCoreStyle::GetDefaultFontStyle("Bold", 10))
						.ColorAndOpacity(FLinearColor::White)
					]
				]
			]
		]
		+SOverlay::Slot() // The note box, next to the drawing
		[
			SNew(SCanvas)
			+SCanvas::Slot()
			.Position(TAttribute<FVector2D>::CreateSP(this, &SCoPresenceAnnotationOverlay::GetNoteBoxPosition))
			.Size(TAttribute<FVector2D>::CreateSP(this, &SCoPresenceAnnotationOverlay::GetNoteBoxSize))
			[
				SNew(SBox)
				.Visibility(this, &SCoPresenceAnnotationOverlay::GetNoteBoxVisibility)
				[
					NoteBox.ToSharedRef()
				]
			]
		]
	];
}

bool SCoPresenceAnnotationOverlay::IsOverControls(const FVector2D& ScreenPosition) const
{
	const bool bOverNoteBox = GetNoteBoxVisibility().IsVisible() && NoteBox->GetTickSpaceGeometry().IsUnderLocation(ScreenPosition);
	return bOverNoteBox || (!bPictureMode && StopButton->GetTickSpaceGeometry().IsUnderLocation(ScreenPosition));
}

void SCoPresenceAnnotationOverlay::FocusNoteBox() const
{
	FSlateApplication::Get().SetKeyboardFocus(NoteText, EFocusCause::SetDirectly);
}

bool SCoPresenceAnnotationOverlay::HasNoteText() const
{
	return !NoteText->GetText().IsEmpty();
}

int32 SCoPresenceAnnotationOverlay::OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry, const FSlateRect& MyCullingRect,
	FSlateWindowElementList& OutDrawElements, int32 LayerId, const FWidgetStyle& InWidgetStyle, bool bParentEnabled) const
{
	if (!bPictureMode)
	{
		PaintFrame(AllottedGeometry, OutDrawElements, LayerId);
	}

	const int32 StrokeLayer = LayerId + 1;
	const TArray<FCoPresenceAnnotationStroke>& Strokes = FCoPresenceAnnotateModule::Get().GetStrokes();
	for (const FCoPresenceAnnotationStroke& Stroke : Strokes)
	{
		PaintStroke(Stroke.Points, AllottedGeometry, OutDrawElements, StrokeLayer);
	}

	const int32 LabelLayer = StrokeLayer + 2;
	if (!bPictureMode)
	{
		for (const FCoPresenceAnnotationStroke& Stroke : Strokes)
		{
			if (!Stroke.Label.IsEmpty())
			{
				PaintLabel(Stroke.Label, Stroke.Points, AllottedGeometry, OutDrawElements, LabelLayer);
			}
		}
	}

	return SCompoundWidget::OnPaint(Args, AllottedGeometry, MyCullingRect, OutDrawElements, LabelLayer + 2, InWidgetStyle, bParentEnabled);
}

FVector2D SCoPresenceAnnotationOverlay::GetNoteBoxPosition() const
{
	using namespace UE::CoPresenceAnnotate::Private;

	const FGeometry& Geometry = GetTickSpaceGeometry();
	const FVector2D Size = Geometry.GetLocalSize();
	const FVector2D BoxSize = GetNoteBoxSize();
	const TArray<FCoPresenceAnnotationStroke>& Strokes = FCoPresenceAnnotateModule::Get().GetStrokes();
	if (Strokes.IsEmpty() || Strokes.Last().Points.IsEmpty())
	{
		return (Size - BoxSize) * 0.5;
	}

	FBox2D Bounds(ForceInit);
	for (const FVector2D& Point : Strokes.Last().Points)
	{
		Bounds += FVector2D(Geometry.AbsoluteToLocal(Point));
	}

	FVector2D Position(Bounds.GetCenter().X - BoxSize.X * 0.5, Bounds.Max.Y + NoteGap);
	if (Position.Y + BoxSize.Y > Size.Y - EdgeMargin)
	{
		Position.Y = Bounds.Min.Y - NoteGap - BoxSize.Y;
	}
	Position.X = FMath::Clamp(Position.X, EdgeMargin, FMath::Max(EdgeMargin, Size.X - BoxSize.X - EdgeMargin));
	Position.Y = FMath::Clamp(Position.Y, EdgeMargin, FMath::Max(EdgeMargin, Size.Y - BoxSize.Y - EdgeMargin));
	return Position;
}

FVector2D SCoPresenceAnnotationOverlay::GetNoteBoxSize() const
{
	return FVector2D(UE::CoPresenceAnnotate::Private::NoteWidth, NoteBox->GetDesiredSize().Y);
}

EVisibility SCoPresenceAnnotationOverlay::GetNoteBoxVisibility() const
{
	const bool bShow = !bPictureMode && FCoPresenceAnnotateModule::Get().HasAnnotation();
	return bShow ? EVisibility::Visible : EVisibility::Collapsed;
}

EVisibility SCoPresenceAnnotationOverlay::GetBannerVisibility() const
{
	// Only its Stop button takes the pointer
	return bPictureMode ? EVisibility::Collapsed : EVisibility::SelfHitTestInvisible;
}

void SCoPresenceAnnotationOverlay::HandleNoteCommitted(const FText& Text, ETextCommit::Type CommitType) const
{
	if (CommitType != ETextCommit::OnEnter)
	{
		return;
	}

	const FString Note = Text.ToString().TrimStartAndEnd();
	if (!Note.IsEmpty())
	{
		FCoPresenceAnnotateModule::Get().SubmitNote(Note);
	}
}

void SCoPresenceAnnotationOverlay::PaintFrame(const FGeometry& AllottedGeometry, FSlateWindowElementList& OutDrawElements, int32 LayerId) const
{
	using namespace UE::CoPresenceAnnotate::Private;

	const FSlateBrush* WhiteBrush = FAppStyle::GetBrush("WhiteBrush");
	const FVector2f Size(AllottedGeometry.GetLocalSize());
	FSlateDrawElement::MakeBox(OutDrawElements, LayerId, AllottedGeometry.ToPaintGeometry(), WhiteBrush, ESlateDrawEffect::None, TintColor);

	const FVector2f BarOrigins[] = {
		FVector2f(0.0f, 0.0f),
		FVector2f(0.0f, Size.Y - FrameThickness),
		FVector2f(0.0f, 0.0f),
		FVector2f(Size.X - FrameThickness, 0.0f),
	};
	const FVector2f BarSizes[] = {
		FVector2f(Size.X, FrameThickness),
		FVector2f(Size.X, FrameThickness),
		FVector2f(FrameThickness, Size.Y),
		FVector2f(FrameThickness, Size.Y),
	};
	for (int32 Index = 0; Index < UE_ARRAY_COUNT(BarOrigins); ++Index)
	{
		FSlateDrawElement::MakeBox(OutDrawElements, LayerId, AllottedGeometry.ToPaintGeometry(BarSizes[Index], FSlateLayoutTransform(BarOrigins[Index])),
			WhiteBrush, ESlateDrawEffect::None, AccentColor);
	}
}

void SCoPresenceAnnotationOverlay::PaintStroke(const TArray<FVector2D>& Points, const FGeometry& AllottedGeometry,
	FSlateWindowElementList& OutDrawElements, int32 LayerId) const
{
	using namespace UE::CoPresenceAnnotate::Private;

	TArray<FVector2D> LocalPoints;
	LocalPoints.Reserve(Points.Num());
	for (const FVector2D& Point : Points)
	{
		LocalPoints.Add(FVector2D(AllottedGeometry.AbsoluteToLocal(Point)));
	}

	// A tap leaves a dot, since a line needs two points apart
	const FBox2D Bounds(LocalPoints);
	if (Bounds.GetSize().Size() < 2.0)
	{
		const FVector2f Origin(Bounds.GetCenter() - FVector2D(DotDiameter * 0.5));
		FSlateDrawElement::MakeBox(OutDrawElements, LayerId + 1, AllottedGeometry.ToPaintGeometry(FVector2f(DotDiameter), FSlateLayoutTransform(Origin)), &DotBrush,
			ESlateDrawEffect::None, DotBrush.GetTint(FWidgetStyle()));
		return;
	}

	FSlateDrawElement::MakeLines(OutDrawElements, LayerId, AllottedGeometry.ToPaintGeometry(), LocalPoints, ESlateDrawEffect::None,
		StrokeShadowColor, true, StrokeShadowThickness);
	FSlateDrawElement::MakeLines(OutDrawElements, LayerId + 1, AllottedGeometry.ToPaintGeometry(), LocalPoints, ESlateDrawEffect::None,
		AccentColor, true, StrokeThickness);
}

void SCoPresenceAnnotationOverlay::PaintLabel(const FString& Label, const TArray<FVector2D>& Points, const FGeometry& AllottedGeometry,
	FSlateWindowElementList& OutDrawElements, int32 LayerId) const
{
	using namespace UE::CoPresenceAnnotate::Private;

	FBox2D Bounds(ForceInit);
	for (const FVector2D& Point : Points)
	{
		Bounds += FVector2D(AllottedGeometry.AbsoluteToLocal(Point));
	}

	const FSlateFontInfo Font = FCoreStyle::GetDefaultFontStyle("Bold", 9);
	const TSharedRef<FSlateFontMeasure> FontMeasure = FSlateApplication::Get().GetRenderer()->GetFontMeasureService();
	const FVector2D TextSize(FontMeasure->Measure(Label, Font));
	const FVector2D BoxSize = TextSize + LabelPadding * 2.0;

	const FVector2D Size(AllottedGeometry.GetLocalSize());
	FVector2D Origin(Bounds.Min.X, Bounds.Min.Y - LabelGap - BoxSize.Y);
	Origin.X = FMath::Clamp(Origin.X, static_cast<double>(FrameThickness), FMath::Max(0.0, Size.X - BoxSize.X - FrameThickness));
	Origin.Y = FMath::Max(Origin.Y, static_cast<double>(FrameThickness));

	// A box takes its color from the tint it is drawn with, not from its brush
	FSlateDrawElement::MakeBox(OutDrawElements, LayerId, AllottedGeometry.ToPaintGeometry(FVector2f(BoxSize), FSlateLayoutTransform(FVector2f(Origin))), &LabelBrush,
		ESlateDrawEffect::None, LabelBrush.GetTint(FWidgetStyle()));
	FSlateDrawElement::MakeText(OutDrawElements, LayerId + 1,
		AllottedGeometry.ToPaintGeometry(FVector2f(TextSize), FSlateLayoutTransform(FVector2f(Origin + LabelPadding))), Label, Font,
		ESlateDrawEffect::None, FLinearColor::White);
}

#undef LOCTEXT_NAMESPACE
