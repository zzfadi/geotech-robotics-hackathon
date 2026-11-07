#include "CoPresenceAnnotateModule.h"

#include "CoPresenceAnnotateCommands.h"
#include "CoPresenceAnnotationCapture.h"
#include "CoPresenceAnnotationInput.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Commands/InputBindingManager.h"
#include "Framework/Commands/UICommandList.h"
#include "Framework/Docking/TabManager.h"
#include "Misc/Base64.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Modules/ModuleManager.h"
#include "SCoPresenceAnnotationOverlay.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Styling/AppStyle.h"
#include "ToolMenus.h"
#include "Widgets/SWindow.h"

DEFINE_LOG_CATEGORY_STATIC(LogCoPresenceAnnotate, Log, All);

#define LOCTEXT_NAMESPACE "CoPresenceAnnotate"

namespace UE::CoPresenceAnnotate::Private
{
	const FName ModuleName(TEXT("CoPresenceAnnotate"));

	// Tools/copresence.sh and Tools/join-mac.sh start agents' editors with -COPRESENCEAGENT; agents don't draw
	const TCHAR* const AgentEditorSwitch = TEXT("COPRESENCEAGENT");

	// Pointer moves shorter than this, in Slate units, add no point to a stroke
	constexpr double MinimumPointSpacing = 2.0;
}

IMPLEMENT_MODULE(FCoPresenceAnnotateModule, CoPresenceAnnotate)

FCoPresenceAnnotateModule& FCoPresenceAnnotateModule::Get()
{
	return FModuleManager::GetModuleChecked<FCoPresenceAnnotateModule>(UE::CoPresenceAnnotate::Private::ModuleName);
}

bool FCoPresenceAnnotateModule::IsAvailable()
{
	return FModuleManager::Get().IsModuleLoaded(UE::CoPresenceAnnotate::Private::ModuleName);
}

void FCoPresenceAnnotateModule::SetAnnotating(bool bAnnotating)
{
	if (bAnnotating == IsAnnotating())
	{
		return;
	}

	if (bAnnotating)
	{
		const TSharedPtr<SWindow> Window = FGlobalTabmanager::Get()->GetRootWindow();
		if (!Window.IsValid())
		{
			return;
		}

		PreviousFocus = FSlateApplication::Get().GetKeyboardFocusedWidget();
		Overlay = SNew(SCoPresenceAnnotationOverlay);
		Window->AddOverlaySlot()
		[
			Overlay.ToSharedRef()
		];
		OverlayWindow = Window;
		return;
	}

	if (const TSharedPtr<SWindow> Window = OverlayWindow.Pin())
	{
		Window->RemoveOverlaySlot(Overlay.ToSharedRef());
	}
	Overlay.Reset();
	OverlayWindow.Reset();
	Strokes.Reset();
	bDrawing = false;
	bFocusNoteBox = false;
	PendingNote.Reset();

	if (const TSharedPtr<SWidget> Focus = PreviousFocus.Pin())
	{
		FSlateApplication::Get().SetKeyboardFocus(Focus, EFocusCause::SetDirectly);
	}
	PreviousFocus.Reset();
}

void FCoPresenceAnnotateModule::ToggleAnnotating()
{
	SetAnnotating(!IsAnnotating());
}

FString FCoPresenceAnnotateModule::TakeAnnotation()
{
	const TSharedPtr<SWindow> Window = OverlayWindow.Pin();
	if (!Window.IsValid() || !Overlay.IsValid() || Strokes.IsEmpty() || bDrawing)
	{
		return FString();
	}

	const TSharedRef<FJsonObject> Annotation = MakeShared<FJsonObject>();
	TArray<TSharedPtr<FJsonValue>> Marks;
	FBox2D Bounds(ForceInit);
	for (const FCoPresenceAnnotationStroke& Stroke : Strokes)
	{
		Bounds += FBox2D(Stroke.Points);
		if (!Stroke.Mark.IsValid())
		{
			continue;
		}
		Marks.Add(MakeShared<FJsonValueObject>(Stroke.Mark));

		// The camera of the first level view drawn on, so agents can look from where the person did
		const TSharedPtr<FJsonObject>* Camera = nullptr;
		if (!Annotation->HasField(TEXT("camera")) && Stroke.Mark->TryGetObjectField(TEXT("camera"), Camera))
		{
			Annotation->SetObjectField(TEXT("camera"), *Camera);
		}
	}
	Annotation->SetArrayField(TEXT("marks"), Marks);

	// The picture shows the strokes on the editor, without the tint, the labels and the note box
	Overlay->SetPictureMode(true);
	TArray64<uint8> Full;
	TArray64<uint8> Detail;
	const bool bCaptured = UE::CoPresenceAnnotate::CaptureWindow(Window.ToSharedRef(), Bounds, Full, Detail);
	Overlay->SetPictureMode(false);
	if (bCaptured)
	{
		const TSharedRef<FJsonObject> Images = MakeShared<FJsonObject>();
		Images->SetStringField(TEXT("full"), FBase64::Encode(Full.GetData(), static_cast<uint32>(Full.Num())));
		if (!Detail.IsEmpty())
		{
			Images->SetStringField(TEXT("detail"), FBase64::Encode(Detail.GetData(), static_cast<uint32>(Detail.Num())));
		}
		Annotation->SetObjectField(TEXT("images"), Images);
	}
	else
	{
		UE_LOG(LogCoPresenceAnnotate, Warning, TEXT("Couldn't take a picture of the editor for the drawing; the agents only get what it marks"));
	}

	FString Json;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Json);
	FJsonSerializer::Serialize(Annotation, Writer);

	SetAnnotating(false);
	return Json;
}

TArray<FCoPresenceAnnotationNote> FCoPresenceAnnotateModule::TakeNotes()
{
	return MoveTemp(Notes);
}

bool FCoPresenceAnnotateModule::IsOnAnnotatedWindow(const FVector2D& ScreenPosition) const
{
	const TSharedPtr<SWindow> Window = OverlayWindow.Pin();
	return Window.IsValid() && Window->GetRectInScreen().ContainsPoint(ScreenPosition);
}

bool FCoPresenceAnnotateModule::IsOverControls(const FVector2D& ScreenPosition) const
{
	return Overlay.IsValid() && Overlay->IsOverControls(ScreenPosition);
}

void FCoPresenceAnnotateModule::BeginStroke(const FVector2D& ScreenPosition)
{
	if (!IsAnnotating())
	{
		return;
	}

	Strokes.AddDefaulted_GetRef().Points.Add(ScreenPosition);
	bDrawing = true;
}

void FCoPresenceAnnotateModule::ExtendStroke(const FVector2D& ScreenPosition)
{
	if (!bDrawing || Strokes.IsEmpty())
	{
		return;
	}

	TArray<FVector2D>& Points = Strokes.Last().Points;
	if (FVector2D::Distance(Points.Last(), ScreenPosition) >= UE::CoPresenceAnnotate::Private::MinimumPointSpacing * GetScale())
	{
		Points.Add(ScreenPosition);
	}
}

void FCoPresenceAnnotateModule::EndStroke()
{
	if (!bDrawing)
	{
		return;
	}
	bDrawing = false;

	const TSharedPtr<SWindow> Window = OverlayWindow.Pin();
	if (!Window.IsValid() || Strokes.IsEmpty())
	{
		return;
	}

	UE::CoPresenceAnnotate::ResolveStroke(Strokes.Last(), Window.ToSharedRef(), Overlay.Get(), GetScale());
	bFocusNoteBox = true;
}

void FCoPresenceAnnotateModule::RemoveLastStroke()
{
	if (!bDrawing && !Strokes.IsEmpty())
	{
		Strokes.Pop();
	}
}

bool FCoPresenceAnnotateModule::HasNoteText() const
{
	return Overlay.IsValid() && Overlay->HasNoteText();
}

void FCoPresenceAnnotateModule::SubmitNote(const FString& Text)
{
	if (HasAnnotation())
	{
		PendingNote = Text;
	}
}

void FCoPresenceAnnotateModule::StartupModule()
{
	using namespace UE::CoPresenceAnnotate::Private;

	if (IsRunningCommandlet() || !FSlateApplication::IsInitialized() || FParse::Param(FCommandLine::Get(), AgentEditorSwitch))
	{
		return;
	}

	NoteHint = LOCTEXT("DefaultNoteHint", "Tell the agents what you mean");
	Captions = MakeUnique<FCoPresenceCaptions>();
	FCoPresenceAnnotateCommands::Register();
	CommandList = MakeShared<FUICommandList>();
	CommandList->MapAction(FCoPresenceAnnotateCommands::Get().ToggleAnnotating,
		FExecuteAction::CreateRaw(this, &FCoPresenceAnnotateModule::ToggleAnnotating),
		FCanExecuteAction(),
		FIsActionChecked::CreateRaw(this, &FCoPresenceAnnotateModule::IsAnnotating));

	// First in line, so the drawing takes the pointer before anything else in the editor sees it
	InputProcessor = MakeShared<FCoPresenceAnnotationInput>();
	FSlateApplication::Get().RegisterInputPreProcessor(InputProcessor, 0);

	TickHandle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateRaw(this, &FCoPresenceAnnotateModule::Tick));
	UToolMenus::RegisterStartupCallback(FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FCoPresenceAnnotateModule::RegisterMenus));
}

void FCoPresenceAnnotateModule::ShutdownModule()
{
	if (!InputProcessor.IsValid())
	{
		return;
	}

	if (FSlateApplication::IsInitialized())
	{
		FSlateApplication::Get().UnregisterInputPreProcessor(InputProcessor);
	}
	InputProcessor.Reset();
	Overlay.Reset();
	Captions.Reset();
	FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);

	if (UObjectInitialized() && UToolMenus::TryGet())
	{
		UToolMenus::UnRegisterStartupCallback(this);
		UToolMenus::UnregisterOwner(this);
	}
	FCoPresenceAnnotateCommands::Unregister();
}

void FCoPresenceAnnotateModule::RegisterMenus()
{
	FToolMenuOwnerScoped OwnerScoped(this);
	UToolMenu* Toolbar = UToolMenus::Get()->ExtendMenu("LevelEditor.LevelEditorToolBar.User");

	// Next to the CoPresence plugin's Agents and Chat buttons
	FToolMenuSection& Section = Toolbar->FindOrAddSection("CoPresence");
	FToolMenuEntry& Entry = Section.AddEntry(FToolMenuEntry::InitToolBarButton(FCoPresenceAnnotateCommands::Get().ToggleAnnotating,
		TAttribute<FText>(), TAttribute<FText>(), FSlateIcon(FAppStyle::GetAppStyleSetName(), "Icons.Edit")));
	Entry.SetCommandList(CommandList);
}

void FCoPresenceAnnotateModule::WarnAboutShortcutClashes() const
{
	const TSharedPtr<FUICommandInfo> Command = FCoPresenceAnnotateCommands::Get().ToggleAnnotating;
	const TSharedRef<const FInputChord> Chord = Command->GetFirstValidChord();
	if (!Chord->IsValidChord())
	{
		return;
	}

	TArray<TSharedPtr<FBindingContext>> Contexts;
	FInputBindingManager::Get().GetKnownInputContexts(Contexts);
	TSet<TSharedPtr<FUICommandInfo>> Clashes;
	for (const TSharedPtr<FBindingContext>& Context : Contexts)
	{
		if (!Context.IsValid() || Context->GetContextName() == Command->GetBindingContext())
		{
			continue;
		}

		// Child contexts report their parents' commands too, so each clash is told once
		const TSharedPtr<FUICommandInfo> Other = FInputBindingManager::Get().GetCommandInfoFromInputChord(Context->GetContextName(), *Chord, false);
		if (!Other.IsValid() || Other == Command || Clashes.Contains(Other))
		{
			continue;
		}
		Clashes.Add(Other);
		UE_LOG(LogCoPresenceAnnotate, Warning, TEXT("The Annotate shortcut %s is also %s (%s). Annotate takes it; change either under Editor Preferences > Keyboard Shortcuts."),
			*Chord->GetInputText().ToString(), *Other->GetLabel().ToString(), *Other->GetBindingContext().ToString());
	}
}

bool FCoPresenceAnnotateModule::Tick(float DeltaTime)
{
	// Other modules register their commands while the editor starts, so this waits for the first tick
	if (!bCheckedShortcut)
	{
		bCheckedShortcut = true;
		WarnAboutShortcutClashes();
	}

	if (bFocusNoteBox && Overlay.IsValid())
	{
		bFocusNoteBox = false;
		Overlay->FocusNoteBox();
	}

	if (PendingNote.IsSet())
	{
		const FString Text = PendingNote.GetValue();
		PendingNote.Reset();
		const FString Annotation = TakeAnnotation();
		if (!Annotation.IsEmpty())
		{
			FCoPresenceAnnotationNote& Note = Notes.AddDefaulted_GetRef();
			Note.Text = Text;
			Note.AnnotationJson = Annotation;
		}
	}
	return true;
}

double FCoPresenceAnnotateModule::GetScale() const
{
	const double Scale = Overlay.IsValid() ? Overlay->GetTickSpaceGeometry().Scale : 0.0;
	return Scale > 0.0 ? Scale : 1.0;
}

#undef LOCTEXT_NAMESPACE
