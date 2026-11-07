#pragma once

#include "CoreMinimal.h"
#include "Framework/Application/IInputProcessor.h"

/**
 * Sees input before any editor widget does. It catches the Annotate shortcut wherever the
 * keyboard is, and while annotating it takes the pointer on the annotated window, so
 * strokes draw instead of clicking, selecting or moving the camera. The note box keeps
 * the pointer and the keyboard, and the banner's Stop button the pointer.
 */
class FCoPresenceAnnotationInput : public IInputProcessor
{
public:
	//~ Begin IInputProcessor
	virtual void Tick(const float DeltaTime, FSlateApplication& SlateApp, TSharedRef<ICursor> Cursor) override;
	virtual bool HandleKeyDownEvent(FSlateApplication& SlateApp, const FKeyEvent& InKeyEvent) override;
	virtual bool HandleMouseMoveEvent(FSlateApplication& SlateApp, const FPointerEvent& MouseEvent) override;
	virtual bool HandleMouseButtonDownEvent(FSlateApplication& SlateApp, const FPointerEvent& MouseEvent) override;
	virtual bool HandleMouseButtonUpEvent(FSlateApplication& SlateApp, const FPointerEvent& MouseEvent) override;
	virtual bool HandleMouseButtonDoubleClickEvent(FSlateApplication& SlateApp, const FPointerEvent& MouseEvent) override;
	virtual bool HandleMouseWheelOrGestureEvent(FSlateApplication& SlateApp, const FPointerEvent& InWheelEvent, const FPointerEvent* InGestureEvent) override;
	virtual const TCHAR* GetDebugName() const override { return TEXT("CoPresenceAnnotate"); }
	//~ End IInputProcessor

private:
	/** Whether the drawing takes this pointer event, rather than the editor under it. */
	bool TakesPointer(const FPointerEvent& MouseEvent) const;
};
