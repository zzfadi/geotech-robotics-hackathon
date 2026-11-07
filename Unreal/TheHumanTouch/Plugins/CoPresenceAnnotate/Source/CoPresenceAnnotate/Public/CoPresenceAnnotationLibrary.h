#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"

#include "CoPresenceAnnotationLibrary.generated.h"

/** What a person typed in the box next to their drawing, with the drawing. */
USTRUCT(BlueprintType)
struct COPRESENCEANNOTATE_API FCoPresenceAnnotationNote
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly, Category = "CoPresence|Annotate")
	FString Text;

	/** The drawing, as TakeAnnotation returns it */
	UPROPERTY(BlueprintReadOnly, Category = "CoPresence|Annotate")
	FString AnnotationJson;
};

/**
 * Annotating: a shortcut (Ctrl+Shift+Space on a Mac, Ctrl+Alt+Space on Windows) tints the
 * editor and lets the person draw anywhere on it, over the level viewport or any panel.
 * Each stroke is worked out as it ends: the actors it circles, crosses or taps, and where
 * it lies in the level, or the text of the panel rows it marks. A box next to the drawing
 * takes what they want; the note it makes, and a drawing taken for a message sent some
 * other way, carry a picture of the editor with the strokes on it.
 */
UCLASS()
class COPRESENCEANNOTATE_API UCoPresenceAnnotationLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Starts annotating, or stops and drops the drawing. */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Annotate")
	static void SetAnnotating(bool bAnnotating);

	UFUNCTION(BlueprintPure, Category = "CoPresence|Annotate")
	static bool IsAnnotating();

	/** Whether there is a drawing that a message sent now would carry. */
	UFUNCTION(BlueprintPure, Category = "CoPresence|Annotate")
	static bool HasAnnotation();

	/**
	 * Takes the drawing for a message sent some other way, such as by voice, and stops
	 * annotating. JSON with what each mark points at ("marks"), the camera of the level view
	 * that was drawn on ("camera"), and base64 JPEG pictures of the editor ("images": "full",
	 * and "detail" when the strokes cover only part of it).
	 * @return Empty when there is no drawing.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Annotate")
	static FString TakeAnnotation();

	/** Notes typed next to a drawing since the last call, which forgets them. */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Annotate")
	static TArray<FCoPresenceAnnotationNote> TakeNotes();

	/** What the note box says before the person types, such as who the note goes to. */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Annotate")
	static void SetNoteHint(const FText& Hint);
};
