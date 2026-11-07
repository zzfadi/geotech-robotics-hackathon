#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "CoPresenceAnnotationLibrary.h"
#include "CoPresenceAnnotationResolver.h"
#include "CoPresenceCaptions.h"
#include "Modules/ModuleInterface.h"

class FCoPresenceAnnotationInput;
class FUICommandList;
class SCoPresenceAnnotationOverlay;
class SWidget;
class SWindow;

class FCoPresenceAnnotateModule : public IModuleInterface
{
public:
	static FCoPresenceAnnotateModule& Get();

	/** Whether the module is loaded; it isn't once the editor is shutting down. */
	static bool IsAvailable();

	bool IsAnnotating() const { return Overlay.IsValid(); }

	/** Starts annotating over the editor's main window, or stops and drops the drawing. */
	void SetAnnotating(bool bAnnotating);
	void ToggleAnnotating();

	bool HasAnnotation() const { return !Strokes.IsEmpty(); }

	/** The drawing as JSON with pictures of the editor, after which annotating stops; see UCoPresenceAnnotationLibrary. */
	FString TakeAnnotation();

	TArray<FCoPresenceAnnotationNote> TakeNotes();

	void SetNoteHint(const FText& Hint) { NoteHint = Hint; }
	FText GetNoteHint() const { return NoteHint; }

	const TArray<FCoPresenceAnnotationStroke>& GetStrokes() const { return Strokes; }

	/** Whether a point in desktop space is on the window being annotated, where the drawing takes the pointer. */
	bool IsOnAnnotatedWindow(const FVector2D& ScreenPosition) const;

	/** Whether a point in desktop space is over the note box or the Stop button, which keep the pointer. */
	bool IsOverControls(const FVector2D& ScreenPosition) const;

	bool IsDrawing() const { return bDrawing; }
	void BeginStroke(const FVector2D& ScreenPosition);
	void ExtendStroke(const FVector2D& ScreenPosition);
	void EndStroke();
	void RemoveLastStroke();

	/** Whether the person has typed anything in the note box yet. */
	bool HasNoteText() const;

	/** Sends the note with the drawing on the next tick, once the note box is out of the picture. */
	void SubmitNote(const FString& Text);

	/** The live captions of a voice call; null in an agent's editor, which shows none. */
	FCoPresenceCaptions* GetCaptions() const { return Captions.Get(); }

	//~ Begin IModuleInterface
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
	//~ End IModuleInterface

private:
	void RegisterMenus();

	/** Logs a warning when another editor command has the Annotate shortcut too. */
	void WarnAboutShortcutClashes() const;

	bool Tick(float DeltaTime);

	/** Desktop pixels per Slate unit on the annotated window, which sizes stroke thresholds. */
	double GetScale() const;

	TSharedPtr<FUICommandList> CommandList;
	TSharedPtr<FCoPresenceAnnotationInput> InputProcessor;
	FTSTicker::FDelegateHandle TickHandle;

	TSharedPtr<SCoPresenceAnnotationOverlay> Overlay;
	TWeakPtr<SWindow> OverlayWindow;

	/** Where the keyboard was before annotating, which gets it back afterwards */
	TWeakPtr<SWidget> PreviousFocus;

	TArray<FCoPresenceAnnotationStroke> Strokes;
	bool bDrawing = false;

	/** The note box shows after the first stroke, so it can only take the keyboard on the next tick */
	bool bFocusNoteBox = false;

	/** A note waiting for its picture, which is taken on the next tick */
	TOptional<FString> PendingNote;

	TArray<FCoPresenceAnnotationNote> Notes;
	FText NoteHint;

	TUniquePtr<FCoPresenceCaptions> Captions;

	bool bCheckedShortcut = false;
};
