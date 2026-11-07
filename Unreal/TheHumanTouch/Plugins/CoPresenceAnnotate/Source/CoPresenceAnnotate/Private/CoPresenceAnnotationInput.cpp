#include "CoPresenceAnnotationInput.h"

#include "CoPresenceAnnotateCommands.h"
#include "CoPresenceAnnotateModule.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Commands/GenericCommands.h"
#include "GenericPlatform/ICursor.h"

void FCoPresenceAnnotationInput::Tick(const float DeltaTime, FSlateApplication& SlateApp, TSharedRef<ICursor> Cursor)
{
	const FCoPresenceAnnotateModule& Module = FCoPresenceAnnotateModule::Get();
	if (!Module.IsAnnotating())
	{
		return;
	}

	const FVector2D Position = Cursor->GetPosition();
	if (Module.IsDrawing() || (Module.IsOnAnnotatedWindow(Position) && !Module.IsOverControls(Position)))
	{
		Cursor->SetType(EMouseCursor::Crosshairs);
	}
}

bool FCoPresenceAnnotationInput::HandleKeyDownEvent(FSlateApplication& SlateApp, const FKeyEvent& InKeyEvent)
{
	FCoPresenceAnnotateModule& Module = FCoPresenceAnnotateModule::Get();
	const FInputChord Chord(InKeyEvent.GetKey(), EModifierKey::FromBools(InKeyEvent.IsControlDown(), InKeyEvent.IsAltDown(),
		InKeyEvent.IsShiftDown(), InKeyEvent.IsCommandDown()));

	if (FCoPresenceAnnotateCommands::Get().ToggleAnnotating->HasActiveChord(Chord))
	{
		if (!InKeyEvent.IsRepeat())
		{
			Module.ToggleAnnotating();
		}
		return true;
	}

	if (!Module.IsAnnotating())
	{
		return false;
	}

	if (InKeyEvent.GetKey() == EKeys::Escape)
	{
		Module.SetAnnotating(false);
		return true;
	}

	// Undo takes back the last stroke until there is text in the note box, which has its own undo
	if (FGenericCommands::Get().Undo->HasActiveChord(Chord) && !Module.HasNoteText())
	{
		Module.RemoveLastStroke();
		return true;
	}

	return false;
}

bool FCoPresenceAnnotationInput::HandleMouseMoveEvent(FSlateApplication& SlateApp, const FPointerEvent& MouseEvent)
{
	if (!TakesPointer(MouseEvent))
	{
		return false;
	}

	FCoPresenceAnnotateModule& Module = FCoPresenceAnnotateModule::Get();
	if (Module.IsDrawing())
	{
		Module.ExtendStroke(MouseEvent.GetScreenSpacePosition());
	}
	return true;
}

bool FCoPresenceAnnotationInput::HandleMouseButtonDownEvent(FSlateApplication& SlateApp, const FPointerEvent& MouseEvent)
{
	if (!TakesPointer(MouseEvent))
	{
		return false;
	}

	// Other buttons do nothing while annotating, rather than reaching the editor under the drawing
	if (MouseEvent.GetEffectingButton() == EKeys::LeftMouseButton)
	{
		FCoPresenceAnnotateModule::Get().BeginStroke(MouseEvent.GetScreenSpacePosition());
	}
	return true;
}

bool FCoPresenceAnnotationInput::HandleMouseButtonUpEvent(FSlateApplication& SlateApp, const FPointerEvent& MouseEvent)
{
	if (!TakesPointer(MouseEvent))
	{
		return false;
	}

	FCoPresenceAnnotateModule& Module = FCoPresenceAnnotateModule::Get();
	if (MouseEvent.GetEffectingButton() == EKeys::LeftMouseButton && Module.IsDrawing())
	{
		Module.ExtendStroke(MouseEvent.GetScreenSpacePosition());
		Module.EndStroke();
	}
	return true;
}

bool FCoPresenceAnnotationInput::HandleMouseButtonDoubleClickEvent(FSlateApplication& SlateApp, const FPointerEvent& MouseEvent)
{
	// A quick second stroke arrives as a double click instead of a button press
	return HandleMouseButtonDownEvent(SlateApp, MouseEvent);
}

bool FCoPresenceAnnotationInput::HandleMouseWheelOrGestureEvent(FSlateApplication& SlateApp, const FPointerEvent& InWheelEvent, const FPointerEvent* InGestureEvent)
{
	return TakesPointer(InWheelEvent);
}

bool FCoPresenceAnnotationInput::TakesPointer(const FPointerEvent& MouseEvent) const
{
	const FCoPresenceAnnotateModule& Module = FCoPresenceAnnotateModule::Get();
	if (!Module.IsAnnotating())
	{
		return false;
	}
	if (Module.IsDrawing())
	{
		return true;
	}

	const FVector2D Position = MouseEvent.GetScreenSpacePosition();
	return Module.IsOnAnnotatedWindow(Position) && !Module.IsOverControls(Position);
}
