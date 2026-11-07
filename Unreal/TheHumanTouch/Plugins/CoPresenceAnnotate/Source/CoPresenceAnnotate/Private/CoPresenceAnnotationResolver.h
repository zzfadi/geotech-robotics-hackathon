#pragma once

#include "CoreMinimal.h"

class FJsonObject;
class SWidget;
class SWindow;

/** One stroke of a drawing, and what it points at once it is finished. */
struct FCoPresenceAnnotationStroke
{
	/** Where the pointer went, in desktop space */
	TArray<FVector2D> Points;

	/** What the stroke points at, as the drawing's JSON lists it */
	TSharedPtr<FJsonObject> Mark;

	/** Shown next to the stroke, so the person sees what it was taken to mean */
	FString Label;
};

namespace UE::CoPresenceAnnotate
{
	/**
	 * Works out what a finished stroke points at. On a level viewport: the actors it circles,
	 * crosses or taps, where it lies in the level, and the camera it was drawn through. On
	 * the editor's panels: the tab and the rows of text it marks. Fills the stroke's Mark
	 * and Label.
	 *
	 * @param Overlay The drawing's own widget, left out when reading the panels' text.
	 * @param Scale Desktop pixels per Slate unit on the window, which sizes what counts as a tap or a loop.
	 */
	void ResolveStroke(FCoPresenceAnnotationStroke& Stroke, const TSharedRef<SWindow>& Window, const SWidget* Overlay, double Scale);
}
