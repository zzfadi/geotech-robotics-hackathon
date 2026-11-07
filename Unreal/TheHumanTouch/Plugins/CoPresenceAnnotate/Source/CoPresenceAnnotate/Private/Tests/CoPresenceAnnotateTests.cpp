#include "CoreMinimal.h"

#if WITH_DEV_AUTOMATION_TESTS

#include "CoPresenceAnnotateCommands.h"
#include "CoPresenceAnnotateModule.h"
#include "CoPresenceAnnotationCapture.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMeshActor.h"
#include "EngineUtils.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Commands/GenericCommands.h"
#include "Framework/Docking/TabManager.h"
#include "ILevelEditor.h"
#include "LevelEditor.h"
#include "LevelEditorViewport.h"
#include "HAL/IConsoleManager.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "SceneView.h"
#include "SLevelViewport.h"
#include "Slate/SceneViewport.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/SWindow.h"
#include "Widgets/Text/STextBlock.h"

// These drive the person's side of annotating through Slate's own input, the way a mouse and
// keyboard would, in an editor started with the CoPresence Annotate plugin and a level open:
//
//   Automation RunTests CoPresence.Annotate
//
// DrawAndSend sends a real note to the session chat, so run it in a test session. Its pictures
// go to Saved/CoPresenceAnnotateTest. By default it circles an actor in view and a line of the
// Outliner; CoPresence.Annotate.TestTarget names an actor to frame and circle instead, and
// CoPresence.Annotate.TestNote sets what the note says.

namespace UE::CoPresenceAnnotate::Tests
{
	const FName DockTabType(TEXT("SDockTab"));
	const TCHAR* const TestNote = TEXT("Test drawing: what did I circle and mark? Tell me in one sentence, and don't change anything.");
	const TCHAR* const PanelTabs[] = {TEXT("Outliner"), TEXT("Details")};

	TAutoConsoleVariable<FString> CVarTestTarget(TEXT("CoPresence.Annotate.TestTarget"), TEXT(""),
		TEXT("Label of the actor CoPresence.Annotate.DrawAndSend frames and circles; empty circles one in view"));
	TAutoConsoleVariable<FString> CVarTestNote(TEXT("CoPresence.Annotate.TestNote"), TEXT(""),
		TEXT("What CoPresence.Annotate.DrawAndSend's note says; empty asks what was marked"));

	FString GetOutputDir()
	{
		return FPaths::ProjectSavedDir() / TEXT("CoPresenceAnnotateTest");
	}

	FModifierKeysState ToModifiers(const FInputChord& Chord)
	{
		return FModifierKeysState(Chord.bShift, false, Chord.bCtrl, false, Chord.bAlt, false, Chord.bCmd, false, false);
	}

	void PressKey(const FKey& Key, const FModifierKeysState& Modifiers = FModifierKeysState())
	{
		FSlateApplication::Get().ProcessKeyDownEvent(FKeyEvent(Key, Modifiers, 0, false, 0, 0));
		FSlateApplication::Get().ProcessKeyUpEvent(FKeyEvent(Key, Modifiers, 0, false, 0, 0));
	}

	void PressAnnotateShortcut()
	{
		const FInputChord& Chord = *FCoPresenceAnnotateCommands::Get().ToggleAnnotating->GetFirstValidChord();
		PressKey(Chord.Key, ToModifiers(Chord));
	}

	void TypeText(const FString& Text)
	{
		for (const TCHAR Character : Text)
		{
			FSlateApplication::Get().ProcessKeyCharEvent(FCharacterEvent(Character, FModifierKeysState(), 0, false));
		}
	}

	/** A stroke with the left mouse button through points in desktop space. */
	void DrawStroke(const TArray<FVector2D>& Points)
	{
		FSlateApplication& Slate = FSlateApplication::Get();
		const TSet<FKey> Pressed = {EKeys::LeftMouseButton};
		Slate.ProcessMouseButtonDownEvent(nullptr, FPointerEvent(0, Points[0], Points[0], Pressed, EKeys::LeftMouseButton, 0.0f, FModifierKeysState()));
		for (int32 Index = 1; Index < Points.Num(); ++Index)
		{
			Slate.ProcessMouseMoveEvent(FPointerEvent(0, Points[Index], Points[Index - 1], Pressed, EKeys::Invalid, 0.0f, FModifierKeysState()));
		}
		Slate.ProcessMouseButtonUpEvent(FPointerEvent(0, Points.Last(), Points.Last(), TSet<FKey>(), EKeys::LeftMouseButton, 0.0f, FModifierKeysState()));
	}

	/** A loose hand-drawn loop around a box, its ends a little apart like a real one. */
	TArray<FVector2D> MakeLoop(const FBox2D& Around, double Margin)
	{
		const FVector2D Center = Around.GetCenter();
		const FVector2D Radius = Around.GetExtent() * 1.2 + FVector2D(Margin);
		TArray<FVector2D> Points;
		constexpr int32 Steps = 48;
		for (int32 Step = 0; Step < Steps; ++Step)
		{
			const double Angle = UE_TWO_PI * Step / Steps;
			Points.Add(Center + FVector2D(FMath::Cos(Angle) * Radius.X, FMath::Sin(Angle) * Radius.Y));
		}
		return Points;
	}

	TSharedPtr<SLevelViewport> GetLevelViewport()
	{
		FLevelEditorModule& LevelEditor = FModuleManager::GetModuleChecked<FLevelEditorModule>(TEXT("LevelEditor"));
		return LevelEditor.GetFirstActiveLevelViewport();
	}

	struct FTarget
	{
		TWeakObjectPtr<AActor> Actor;

		/** Where it shows, in desktop space */
		FBox2D ScreenBox;
	};

	/** A static mesh actor that shows whole and fairly small in the viewport, not hidden behind anything. */
	TOptional<FTarget> FindTarget(SLevelViewport& LevelViewport, const FString& Label)
	{
		FLevelEditorViewportClient& Client = LevelViewport.GetLevelViewportClient();
		const TSharedPtr<FSceneViewport> SceneViewport = LevelViewport.GetSceneViewport();
		FSceneViewFamilyContext ViewFamily(FSceneViewFamily::ConstructionValues(SceneViewport.Get(), Client.GetScene(), Client.EngineShowFlags).SetRealtimeUpdate(true));
		const FSceneView* View = Client.CalcSceneView(&ViewFamily);
		const FVector2D Size(SceneViewport->GetSizeXY());
		const FGeometry& Geometry = SceneViewport->GetCachedGeometry();

		for (TActorIterator<AStaticMeshActor> It(Client.GetWorld()); It; ++It)
		{
			AActor* Actor = *It;
			if (Actor->IsHiddenEd() || (!Label.IsEmpty() && Actor->GetActorLabel() != Label))
			{
				continue;
			}

			FVector Origin;
			FVector Extent;
			Actor->GetActorBounds(false, Origin, Extent);
			FBox2D PixelBox(ForceInit);
			bool bInFront = true;
			for (int32 Corner = 0; Corner < 8 && bInFront; ++Corner)
			{
				const FVector Offset((Corner & 1) ? 1.0 : -1.0, (Corner & 2) ? 1.0 : -1.0, (Corner & 4) ? 1.0 : -1.0);
				FVector2D Pixel;
				bInFront = View->WorldToPixel(Origin + Extent * Offset, Pixel);
				PixelBox += Pixel;
			}
			const FVector2D BoxSize = PixelBox.GetSize();
			if (!bInFront || PixelBox.Min.X < Size.X * 0.15 || PixelBox.Min.Y < Size.Y * 0.15
				|| PixelBox.Max.X > Size.X * 0.85 || PixelBox.Max.Y > Size.Y * 0.85
				|| BoxSize.X < Size.X * 0.03 || BoxSize.X > Size.X * 0.25)
			{
				continue;
			}

			const FVector2D Center = PixelBox.GetCenter();
			const HActor* Shown = HitProxyCast<HActor>(SceneViewport->GetHitProxy(FMath::RoundToInt32(Center.X), FMath::RoundToInt32(Center.Y)));
			if (!Shown || Shown->Actor != Actor)
			{
				continue;
			}

			const FVector2D LocalPerPixel = FVector2D(Geometry.GetLocalSize()) / Size;
			return FTarget{Actor, FBox2D(FVector2D(Geometry.LocalToAbsolute(PixelBox.Min * LocalPerPixel)),
				FVector2D(Geometry.LocalToAbsolute(PixelBox.Max * LocalPerPixel)))};
		}
		return {};
	}

	/** Points the viewport at the named actor, or at any mid-sized static mesh actor. */
	void FrameSomething(SLevelViewport& LevelViewport, const FString& Label)
	{
		FLevelEditorViewportClient& Client = LevelViewport.GetLevelViewportClient();
		for (TActorIterator<AStaticMeshActor> It(Client.GetWorld()); It; ++It)
		{
			const FBox Bounds = It->GetComponentsBoundingBox(true);
			const double Size = Bounds.GetExtent().GetMax();
			const bool bWanted = Label.IsEmpty() ? Size > 30.0 && Size < 200.0 : It->GetActorLabel() == Label;
			if (!It->IsHiddenEd() && bWanted)
			{
				Client.FocusViewportOnBox(Bounds.ExpandBy(Size * 2.0), true);
				return;
			}
		}
	}

	struct FPanelText
	{
		FString Tab;
		FString Text;
		FBox2D ScreenBox;
	};

	/** The first line of text that shows in a widget. */
	bool FindTextIn(const TSharedRef<SWidget>& Widget, const FSlateRect& Window, FPanelText& OutText)
	{
		if (!Widget->GetVisibility().IsVisible())
		{
			return false;
		}
		if (Widget->GetType() == STextBlock::StaticWidgetClass().GetWidgetType())
		{
			const FString Text = StaticCastSharedRef<STextBlock>(Widget)->GetText().ToString().TrimStartAndEnd();
			const FSlateRect Rect = Widget->GetPaintSpaceGeometry().GetLayoutBoundingRect();
			if (Text.Len() > 2 && Rect.GetArea() > 0.0 && FSlateRect::IsRectangleContained(Window, Rect))
			{
				OutText.Text = Text;
				OutText.ScreenBox = FBox2D(FVector2D(Rect.GetTopLeft()), FVector2D(Rect.GetBottomRight()));
				return true;
			}
		}
		FChildren* Children = Widget->GetChildren();
		for (int32 Index = 0; Index < Children->Num(); ++Index)
		{
			if (FindTextIn(Children->GetChildAt(Index), Window, OutText))
			{
				return true;
			}
		}
		return false;
	}

	bool FindDockTabs(const TSharedRef<SWidget>& Widget, TArray<TSharedRef<SDockTab>>& OutTabs)
	{
		if (Widget->GetType() == DockTabType)
		{
			OutTabs.Add(StaticCastSharedRef<SDockTab>(Widget));
		}
		FChildren* Children = Widget->GetChildren();
		for (int32 Index = 0; Index < Children->Num(); ++Index)
		{
			FindDockTabs(Children->GetChildAt(Index), OutTabs);
		}
		return !OutTabs.IsEmpty();
	}

	/** A line of text in the Outliner or Details tab, whichever is in front. */
	TOptional<FPanelText> FindPanelText(const TSharedRef<SWindow>& Window)
	{
		TArray<TSharedRef<SDockTab>> Tabs;
		FindDockTabs(Window, Tabs);
		for (const TCHAR* Wanted : PanelTabs)
		{
			for (const TSharedRef<SDockTab>& Tab : Tabs)
			{
				FPanelText Found;
				Found.Tab = Tab->GetTabLabel().ToString();
				if (Tab->IsForeground() && Found.Tab == Wanted && FindTextIn(Tab->GetContent(), Window->GetRectInScreen(), Found))
				{
					return Found;
				}
			}
		}
		return {};
	}

	void SavePicture(const TSharedRef<SWindow>& Window, const FString& Name)
	{
		TArray64<uint8> Full;
		TArray64<uint8> Detail;
		if (CaptureWindow(Window, FBox2D(ForceInit), Full, Detail))
		{
			FFileHelper::SaveArrayToFile(Full, *(GetOutputDir() / Name));
		}
	}

	bool MarkListsActor(const TSharedPtr<FJsonObject>& Mark, const AActor& Actor)
	{
		const TArray<TSharedPtr<FJsonValue>>* Actors = nullptr;
		if (!Mark.IsValid() || !Mark->TryGetArrayField(TEXT("actors"), Actors))
		{
			return false;
		}
		return Actors->ContainsByPredicate([&Actor](const TSharedPtr<FJsonValue>& Value)
		{
			return Value->AsObject()->GetStringField(TEXT("path")) == Actor.GetPathName();
		});
	}

	struct FDrawState
	{
		TOptional<FTarget> Target;
		TOptional<FPanelText> PanelText;
		bool bStopped = false;
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FCoPresenceAnnotateEscapeTest, "CoPresence.Annotate.EscapeDropsDrawing",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FCoPresenceAnnotateEscapeTest::RunTest(const FString& Parameters)
{
	using namespace UE::CoPresenceAnnotate::Tests;

	if (!FCoPresenceAnnotateCommands::IsRegistered() || !GetLevelViewport().IsValid())
	{
		AddError(TEXT("Needs a person's editor with the CoPresence Annotate plugin and a level open"));
		return false;
	}

	FCoPresenceAnnotateModule& Module = FCoPresenceAnnotateModule::Get();
	Module.SetAnnotating(false);
	PressAnnotateShortcut();
	TestTrue(TEXT("The shortcut starts annotating"), Module.IsAnnotating());

	const FBox2D Viewport(FVector2D(GetLevelViewport()->GetSceneViewport()->GetCachedGeometry().GetAbsolutePosition()),
		FVector2D(GetLevelViewport()->GetSceneViewport()->GetCachedGeometry().GetAbsolutePositionAtCoordinates(FVector2f(1.0f, 1.0f))));
	DrawStroke({Viewport.GetCenter(), Viewport.GetCenter() + FVector2D(120.0, 40.0)});
	TestEqual(TEXT("The stroke is drawn"), Module.GetStrokes().Num(), 1);
	TestFalse(TEXT("The stroke ends with the button"), Module.IsDrawing());

	DrawStroke({Viewport.GetCenter() + FVector2D(0.0, 80.0), Viewport.GetCenter() + FVector2D(150.0, 80.0)});
	const FInputChord& Undo = *FGenericCommands::Get().Undo->GetFirstValidChord();
	PressKey(Undo.Key, ToModifiers(Undo));
	TestEqual(TEXT("Undo takes back the last stroke"), Module.GetStrokes().Num(), 1);
	TestTrue(TEXT("Undo keeps annotating"), Module.IsAnnotating());

	PressKey(EKeys::Escape);
	TestFalse(TEXT("Escape stops annotating"), Module.IsAnnotating());
	TestFalse(TEXT("Escape drops the drawing"), Module.HasAnnotation());

	PressAnnotateShortcut();
	PressAnnotateShortcut();
	TestFalse(TEXT("The shortcut stops annotating too"), Module.IsAnnotating());
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FCoPresenceAnnotateStopTest, "CoPresence.Annotate.StopButtonEndsIt",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FCoPresenceAnnotateStopTest::RunTest(const FString& Parameters)
{
	using namespace UE::CoPresenceAnnotate::Tests;

	const TSharedPtr<SWindow> Window = FGlobalTabmanager::Get()->GetRootWindow();
	if (!FCoPresenceAnnotateCommands::IsRegistered() || !Window.IsValid())
	{
		AddError(TEXT("Needs a person's editor with the CoPresence Annotate plugin"));
		return false;
	}

	FCoPresenceAnnotateModule& Module = FCoPresenceAnnotateModule::Get();
	Module.SetAnnotating(false);
	PressAnnotateShortcut();
	TestTrue(TEXT("The shortcut starts annotating"), Module.IsAnnotating());

	// The banner lays itself out on the next frames
	ADD_LATENT_AUTOMATION_COMMAND(FDelayedFunctionLatentCommand([this, Window, &Module]()
	{
		// With nothing drawn there is no note box, so the only control the pointer reaches is Stop,
		// in the banner along the top of the window
		const FSlateRect WindowRect = Window->GetRectInScreen();
		TOptional<FVector2D> StopPoint;
		for (double Y = WindowRect.Top; Y < WindowRect.Top + 400.0 && !StopPoint.IsSet(); Y += 3.0)
		{
			for (double X = WindowRect.Left; X < WindowRect.Right; X += 3.0)
			{
				if (Module.IsOverControls(FVector2D(X, Y)))
				{
					StopPoint = FVector2D(X + 3.0, Y + 3.0);
					break;
				}
			}
		}
		TestTrue(TEXT("The banner has a Stop button the pointer reaches"), StopPoint.IsSet());
		if (!StopPoint.IsSet())
		{
			Module.SetAnnotating(false);
			return;
		}

		const FVector2D Point = *StopPoint;
		const TSet<FKey> Pressed = {EKeys::LeftMouseButton};
		FSlateApplication::Get().ProcessMouseMoveEvent(FPointerEvent(0, Point, Point, TSet<FKey>(), EKeys::Invalid, 0.0f, FModifierKeysState()));
		FSlateApplication::Get().ProcessMouseButtonDownEvent(nullptr, FPointerEvent(0, Point, Point, Pressed, EKeys::LeftMouseButton, 0.0f, FModifierKeysState()));
		FSlateApplication::Get().ProcessMouseButtonUpEvent(FPointerEvent(0, Point, Point, TSet<FKey>(), EKeys::LeftMouseButton, 0.0f, FModifierKeysState()));
		TestFalse(TEXT("Clicking Stop draws nothing"), Module.HasAnnotation());
		TestFalse(TEXT("Clicking Stop stops annotating"), Module.IsAnnotating());
	}, 0.5f));
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FCoPresenceAnnotateDrawTest, "CoPresence.Annotate.DrawAndSend",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FCoPresenceAnnotateDrawTest::RunTest(const FString& Parameters)
{
	using namespace UE::CoPresenceAnnotate::Tests;

	const TSharedPtr<SLevelViewport> LevelViewport = GetLevelViewport();
	const TSharedPtr<SWindow> Window = FGlobalTabmanager::Get()->GetRootWindow();
	if (!FCoPresenceAnnotateCommands::IsRegistered() || !LevelViewport.IsValid() || !Window.IsValid())
	{
		AddError(TEXT("Needs a person's editor with the CoPresence Annotate plugin and a level open"));
		return false;
	}

	FCoPresenceAnnotateModule& Module = FCoPresenceAnnotateModule::Get();
	Module.SetAnnotating(false);
	const FString TargetLabel = CVarTestTarget.GetValueOnGameThread();
	const FString Note = CVarTestNote.GetValueOnGameThread().IsEmpty() ? FString(TestNote) : CVarTestNote.GetValueOnGameThread();
	const TSharedRef<FDrawState> State = MakeShared<FDrawState>();
	if (TargetLabel.IsEmpty())
	{
		State->Target = FindTarget(*LevelViewport, TargetLabel);
	}
	if (!State->Target.IsSet())
	{
		FrameSomething(*LevelViewport, TargetLabel);
	}

	// The viewport draws the framed view, and the drawing lays itself out, before the strokes
	ADD_LATENT_AUTOMATION_COMMAND(FDelayedFunctionLatentCommand([this, State, LevelViewport, TargetLabel, &Module]()
	{
		if (!State->Target.IsSet())
		{
			State->Target = FindTarget(*LevelViewport, TargetLabel);
		}
		PressAnnotateShortcut();
		TestTrue(TEXT("The shortcut starts annotating"), Module.IsAnnotating());
	}, 1.0f));

	ADD_LATENT_AUTOMATION_COMMAND(FDelayedFunctionLatentCommand([this, State, Window, TargetLabel, &Module]()
	{
		if (!State->Target.IsSet() || !State->Target->Actor.IsValid())
		{
			AddError(TEXT("No static mesh actor shows whole in the viewport to circle"));
			State->bStopped = true;
			Module.SetAnnotating(false);
			return;
		}

		const AActor& Actor = *State->Target->Actor;
		DrawStroke(MakeLoop(State->Target->ScreenBox, 12.0));
		const FCoPresenceAnnotationStroke& Stroke = Module.GetStrokes().Last();
		AddInfo(FString::Printf(TEXT("Circled %s, shown as \"%s\""), *Actor.GetActorLabel(), *Stroke.Label));
		TestTrue(TEXT("A loop around an actor lists it"), MarkListsActor(Stroke.Mark, Actor));
		TestEqual(TEXT("A loop is a loop"), Stroke.Mark.IsValid() ? Stroke.Mark->GetStringField(TEXT("shape")) : FString(), FString(TEXT("loop")));
		TestTrue(TEXT("The level mark has the camera"), Stroke.Mark.IsValid() && Stroke.Mark->HasField(TEXT("camera")));
		if (!TargetLabel.IsEmpty())
		{
			return;
		}

		State->PanelText = FindPanelText(Window.ToSharedRef());
		if (!State->PanelText.IsSet())
		{
			AddWarning(TEXT("Neither the Outliner nor the Details tab is in front, so nothing was marked on a panel"));
			return;
		}
		DrawStroke(MakeLoop(State->PanelText->ScreenBox, 4.0));
		const FCoPresenceAnnotationStroke& PanelStroke = Module.GetStrokes().Last();
		AddInfo(FString::Printf(TEXT("Circled \"%s\" in %s, shown as \"%s\""), *State->PanelText->Text, *State->PanelText->Tab, *PanelStroke.Label));
		TestEqual(TEXT("A loop on a panel is on the editor"), PanelStroke.Mark->GetStringField(TEXT("on")), FString(TEXT("editor")));
		TestEqual(TEXT("A loop on a panel names its tab"), PanelStroke.Mark->GetStringField(TEXT("tab")), State->PanelText->Tab);
		TestTrue(TEXT("A loop on a panel reads its row"), PanelStroke.Mark->GetArrayField(TEXT("rows")).ContainsByPredicate(
			[&State](const TSharedPtr<FJsonValue>& Row) { return Row->AsString().Contains(State->PanelText->Text); }));
	}, 0.5f));

	// The note box takes the keyboard on the tick after a stroke
	ADD_LATENT_AUTOMATION_COMMAND(FDelayedFunctionLatentCommand([this, State, Window, Note, &Module]()
	{
		if (State->bStopped)
		{
			return;
		}
		TypeText(Note);
		TestTrue(TEXT("Typing goes into the note box"), Module.HasNoteText());
		SavePicture(Window.ToSharedRef(), TEXT("overlay.jpg"));
		PressKey(EKeys::Enter);
	}, 0.5f));

	ADD_LATENT_AUTOMATION_COMMAND(FDelayedFunctionLatentCommand([this, State, &Module]()
	{
		if (!State->bStopped)
		{
			TestFalse(TEXT("Sending the note stops annotating"), Module.IsAnnotating());
		}
	}, 0.5f));
	return true;
}

#endif
