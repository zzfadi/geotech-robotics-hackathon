#pragma once

#include "CoreMinimal.h"
#include "Brushes/SlateRoundedBoxBrush.h"
#include "Widgets/SCompoundWidget.h"

class SEditableTextBox;

/**
 * Drawn over the whole editor window while annotating: a warm tint and frame that show the
 * editor is in annotation mode, the strokes, a label by each stroke saying what it was
 * taken to point at, and a box for what the person wants. It takes no pointer input
 * itself; FCoPresenceAnnotationInput does, and leaves the note box and the Stop button theirs.
 */
class SCoPresenceAnnotationOverlay : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SCoPresenceAnnotationOverlay) {}
	SLATE_END_ARGS()

	SCoPresenceAnnotationOverlay();

	void Construct(const FArguments& InArgs);

	/** Whether a point in desktop space is over the note box or the banner's Stop button, which take the pointer. */
	bool IsOverControls(const FVector2D& ScreenPosition) const;

	void FocusNoteBox() const;
	bool HasNoteText() const;

	/** While set, only the strokes are drawn, for the picture the agents get. */
	void SetPictureMode(bool bInPictureMode) { bPictureMode = bInPictureMode; }

	//~ Begin SWidget
	virtual int32 OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry, const FSlateRect& MyCullingRect,
		FSlateWindowElementList& OutDrawElements, int32 LayerId, const FWidgetStyle& InWidgetStyle, bool bParentEnabled) const override;
	//~ End SWidget

private:
	/** Below the last stroke, or above it when there is no room below. */
	FVector2D GetNoteBoxPosition() const;
	FVector2D GetNoteBoxSize() const;
	EVisibility GetNoteBoxVisibility() const;
	EVisibility GetBannerVisibility() const;
	void HandleNoteCommitted(const FText& Text, ETextCommit::Type CommitType) const;

	void PaintFrame(const FGeometry& AllottedGeometry, FSlateWindowElementList& OutDrawElements, int32 LayerId) const;
	void PaintStroke(const TArray<FVector2D>& Points, const FGeometry& AllottedGeometry, FSlateWindowElementList& OutDrawElements, int32 LayerId) const;

	/** A tag above the stroke with what it was taken to point at. */
	void PaintLabel(const FString& Label, const TArray<FVector2D>& Points, const FGeometry& AllottedGeometry,
		FSlateWindowElementList& OutDrawElements, int32 LayerId) const;

	TSharedPtr<SWidget> NoteBox;
	TSharedPtr<SEditableTextBox> NoteText;
	TSharedPtr<SWidget> StopButton;

	FSlateRoundedBoxBrush LabelBrush;
	FSlateRoundedBoxBrush DotBrush;
	FSlateRoundedBoxBrush BannerBrush;
	FSlateRoundedBoxBrush NoteBrush;

	bool bPictureMode = false;
};
