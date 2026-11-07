#include "CoPresenceAnnotationResolver.h"

#include "CollisionQueryParams.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/HitResult.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Framework/Application/SlateApplication.h"
#include "ILevelEditor.h"
#include "LevelEditor.h"
#include "LevelEditorViewport.h"
#include "Layout/WidgetPath.h"
#include "SceneView.h"
#include "SLevelViewport.h"
#include "Slate/SceneViewport.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Input/SEditableText.h"
#include "Widgets/SWindow.h"
#include "Widgets/Text/STextBlock.h"

namespace UE::CoPresenceAnnotate::Private
{
	enum class EShape : uint8
	{
		Dot,
		Line,
		Loop,
	};

	// Strokes smaller than this, in Slate units, are taps
	constexpr double DotSize = 12.0;

	// A stroke whose ends come this close, as a share of its size or in Slate units, is a loop
	constexpr double LoopGapShare = 0.3;
	constexpr double LoopGapMinimum = 24.0;

	// A loop is weighed against what shows around it, this share of its size out on each side.
	// An actor is circled when at least CircledShare of what shows of it there is inside the
	// loop, so a chair in a loop counts and the floor around it doesn't
	constexpr double LoopSurroundShare = 0.5;
	constexpr double CircledShare = 0.5;
	constexpr int32 CircledMinimumPixels = 12;
	constexpr double CircledMinimumShare = 0.002;

	// A line runs over an actor that shows within LineReachPixels of at least LineOverShare of it
	constexpr int32 LineReachPixels = 3;
	constexpr double LineOverShare = 0.15;
	constexpr double LineSampleSpacing = 3.0;

	// A tap points at what shows there, or else at what shows most within this many pixels
	constexpr int32 DotReachPixels = 6;

	constexpr int32 MaxLoopActors = 25;
	constexpr int32 MaxLineActors = 8;
	constexpr int32 MaxPathPoints = 16;
	constexpr int32 MaxRows = 12;
	constexpr int32 MaxRowLength = 200;
	constexpr int32 MaxLabelLength = 40;
	constexpr double TraceLength = 1000000.0;

	// Text this close to a line or tap, in Slate units, counts as marked by it
	constexpr double TextReach = 4.0;

	const FName DockTabType(TEXT("SDockTab"));

	struct FMarkedActor
	{
		AActor* Actor = nullptr;

		/** For a loop, the share of the actor inside it; for a line, the share of the line over the actor */
		double Share = 1.0;

		/** How many pixels of it the mark covers, which orders the actors */
		int32 Pixels = 0;
	};

	/** The level viewport a stroke is drawn on, with the stroke in the viewport's pixels. */
	struct FViewportStroke
	{
		TSharedPtr<SLevelViewport> LevelViewport;
		TSharedPtr<FSceneViewport> SceneViewport;
		TArray<FVector2D> Pixels;
	};

	const TCHAR* GetShapeName(EShape Shape)
	{
		switch (Shape)
		{
		case EShape::Dot:
			return TEXT("dot");
		case EShape::Line:
			return TEXT("line");
		default:
			return TEXT("loop");
		}
	}

	EShape GetShape(const TArray<FVector2D>& Points, double Scale)
	{
		const FBox2D Bounds(Points);
		const double Size = Bounds.GetSize().Size();
		if (Size < DotSize * Scale)
		{
			return EShape::Dot;
		}

		const double Gap = FVector2D::Distance(Points[0], Points.Last());
		return Gap <= FMath::Max(LoopGapMinimum * Scale, Size * LoopGapShare) ? EShape::Loop : EShape::Line;
	}

	double GetLength(const TArray<FVector2D>& Points)
	{
		double Length = 0.0;
		for (int32 Index = 1; Index < Points.Num(); ++Index)
		{
			Length += FVector2D::Distance(Points[Index - 1], Points[Index]);
		}
		return Length;
	}

	/** Points every Spacing along a stroke, both ends included. */
	TArray<FVector2D> Resample(const TArray<FVector2D>& Points, double Spacing)
	{
		TArray<FVector2D> Samples = {Points[0]};
		if (Spacing <= 0.0)
		{
			return Samples;
		}

		// How far the stroke has gone since the last sample
		double SinceSample = 0.0;
		for (int32 Index = 1; Index < Points.Num(); ++Index)
		{
			const FVector2D Start = Points[Index - 1];
			const FVector2D Segment = Points[Index] - Start;
			const double Length = Segment.Size();
			double Along = Spacing - SinceSample;
			while (Along <= Length)
			{
				Samples.Add(Start + Segment * (Along / Length));
				Along += Spacing;
			}
			SinceSample = Length - (Along - Spacing);
		}

		if (!Samples.Last().Equals(Points.Last()))
		{
			Samples.Add(Points.Last());
		}
		return Samples;
	}

	/** Whether a point is inside a loop of points, which closes from its last point to its first. */
	bool IsInLoop(const TArray<FVector2D>& Loop, const FVector2D& Point)
	{
		bool bInside = false;
		for (int32 Index = 0, Previous = Loop.Num() - 1; Index < Loop.Num(); Previous = Index++)
		{
			const FVector2D& Start = Loop[Previous];
			const FVector2D& End = Loop[Index];
			if ((Start.Y > Point.Y) != (End.Y > Point.Y)
				&& Point.X < Start.X + (Point.Y - Start.Y) / (End.Y - Start.Y) * (End.X - Start.X))
			{
				bInside = !bInside;
			}
		}
		return bInside;
	}

	/** Visits each pixel of a rectangle that is inside a loop, row by row. */
	void ForEachPixelInLoop(const TArray<FVector2D>& Loop, const FIntRect& Rect, TFunctionRef<void(int32, int32)> Visit)
	{
		TArray<double> Crossings;
		for (int32 Y = Rect.Min.Y; Y < Rect.Max.Y; ++Y)
		{
			const double RowY = Y + 0.5;
			Crossings.Reset();
			for (int32 Index = 0, Previous = Loop.Num() - 1; Index < Loop.Num(); Previous = Index++)
			{
				const FVector2D& Start = Loop[Previous];
				const FVector2D& End = Loop[Index];
				if ((Start.Y > RowY) != (End.Y > RowY))
				{
					Crossings.Add(Start.X + (RowY - Start.Y) / (End.Y - Start.Y) * (End.X - Start.X));
				}
			}
			Crossings.Sort();

			// Between each pair of crossings the row is inside
			for (int32 Index = 0; Index + 1 < Crossings.Num(); Index += 2)
			{
				const int32 First = FMath::Max(Rect.Min.X, FMath::CeilToInt32(Crossings[Index] - 0.5));
				const int32 Last = FMath::Min(Rect.Max.X, FMath::CeilToInt32(Crossings[Index + 1] - 0.5));
				for (int32 X = First; X < Last; ++X)
				{
					Visit(X, Y);
				}
			}
		}
	}

	FIntRect ToPixelRect(const FBox2D& Box, const FIntPoint& ViewportSize)
	{
		FIntRect Rect(FMath::FloorToInt32(Box.Min.X), FMath::FloorToInt32(Box.Min.Y), FMath::CeilToInt32(Box.Max.X) + 1, FMath::CeilToInt32(Box.Max.Y) + 1);
		Rect.Clip(FIntRect(FIntPoint::ZeroValue, ViewportSize));
		return Rect;
	}

	/** The actor a hit proxy shows, leaving out the ones an editor keeps to itself, such as presence avatars. */
	AActor* GetActor(HHitProxy* HitProxy)
	{
		AActor* Actor = nullptr;
		if (const HActor* ActorProxy = HitProxyCast<HActor>(HitProxy))
		{
			Actor = ActorProxy->Actor;
		}
		else if (const HInstancedStaticMeshInstance* InstanceProxy = HitProxyCast<HInstancedStaticMeshInstance>(HitProxy))
		{
			Actor = InstanceProxy->Component ? InstanceProxy->Component->GetOwner() : nullptr;
		}
		return Actor && !Actor->HasAnyFlags(RF_Transient) ? Actor : nullptr;
	}

	/** Which actor shows at each pixel of part of a viewport. */
	class FActorMap
	{
	public:
		FActorMap(FViewport& Viewport, const FIntRect& InRect)
			: Rect(InRect)
		{
			Actors.SetNumZeroed(Rect.Area());
			if (Rect.Area() <= 0)
			{
				return;
			}

			TArray<HHitProxy*> HitProxies;
			Viewport.GetHitProxyMap(Rect, HitProxies);
			HHitProxy* LastProxy = nullptr;
			AActor* LastActor = nullptr;
			for (int32 Index = 0; Index < HitProxies.Num() && Index < Actors.Num(); ++Index)
			{
				// Neighbouring pixels mostly show the same thing
				if (HitProxies[Index] != LastProxy)
				{
					LastProxy = HitProxies[Index];
					LastActor = GetActor(LastProxy);
				}
				Actors[Index] = LastActor;
			}
		}

		const FIntRect& GetRect() const { return Rect; }
		const TArray<AActor*>& GetActors() const { return Actors; }

		AActor* At(int32 X, int32 Y) const
		{
			if (!Rect.Contains(FIntPoint(X, Y)))
			{
				return nullptr;
			}
			return Actors[(Y - Rect.Min.Y) * Rect.Width() + (X - Rect.Min.X)];
		}

	private:
		FIntRect Rect;
		TArray<AActor*> Actors;
	};

	void SortByPixels(TArray<FMarkedActor>& Marked)
	{
		Marked.Sort([](const FMarkedActor& First, const FMarkedActor& Second)
		{
			return First.Pixels > Second.Pixels;
		});
	}

	/** The actors a loop goes around, and the actor that shows most inside it. */
	TArray<FMarkedActor> GetCircledActors(FViewport& Viewport, const TArray<FVector2D>& Loop, AActor*& OutMostInside)
	{
		struct FCount
		{
			int32 Inside = 0;
			int32 Around = 0;
		};

		const FBox2D Bounds(Loop);
		const FActorMap Map(Viewport, ToPixelRect(Bounds.ExpandBy(Bounds.GetSize() * LoopSurroundShare), Viewport.GetSizeXY()));
		TMap<AActor*, FCount> Counts;
		for (AActor* Actor : Map.GetActors())
		{
			if (Actor)
			{
				++Counts.FindOrAdd(Actor).Around;
			}
		}

		int32 LoopPixels = 0;
		ForEachPixelInLoop(Loop, Map.GetRect(), [&Map, &Counts, &LoopPixels](int32 X, int32 Y)
		{
			++LoopPixels;
			if (AActor* Actor = Map.At(X, Y))
			{
				++Counts.FindOrAdd(Actor).Inside;
			}
		});

		const int32 MinimumPixels = FMath::Max(CircledMinimumPixels, FMath::RoundToInt32(LoopPixels * CircledMinimumShare));
		TArray<FMarkedActor> Circled;
		int32 MostInsidePixels = 0;
		OutMostInside = nullptr;
		for (const TPair<AActor*, FCount>& Count : Counts)
		{
			if (Count.Value.Inside > MostInsidePixels)
			{
				MostInsidePixels = Count.Value.Inside;
				OutMostInside = Count.Key;
			}

			const double Share = static_cast<double>(Count.Value.Inside) / Count.Value.Around;
			if (Count.Value.Inside >= MinimumPixels && Share >= CircledShare)
			{
				Circled.Add({Count.Key, Share, Count.Value.Inside});
			}
		}

		SortByPixels(Circled);
		if (Circled.Num() > MaxLoopActors)
		{
			Circled.SetNum(MaxLoopActors);
		}
		return Circled;
	}

	/** The actors a line runs over, the ones it runs over most first. */
	TArray<FMarkedActor> GetActorsUnderLine(FViewport& Viewport, const TArray<FVector2D>& Line)
	{
		const FBox2D Bounds(Line);
		const FActorMap Map(Viewport, ToPixelRect(Bounds.ExpandBy(FVector2D(LineReachPixels + 1)), Viewport.GetSizeXY()));
		const TArray<FVector2D> Samples = Resample(Line, LineSampleSpacing);

		TMap<AActor*, int32> Counts;
		TSet<AActor*> NearSample;
		for (const FVector2D& Sample : Samples)
		{
			NearSample.Reset();
			const int32 SampleX = FMath::RoundToInt32(Sample.X);
			const int32 SampleY = FMath::RoundToInt32(Sample.Y);
			for (int32 OffsetY = -LineReachPixels; OffsetY <= LineReachPixels; ++OffsetY)
			{
				for (int32 OffsetX = -LineReachPixels; OffsetX <= LineReachPixels; ++OffsetX)
				{
					if (AActor* Actor = Map.At(SampleX + OffsetX, SampleY + OffsetY))
					{
						NearSample.Add(Actor);
					}
				}
			}
			for (AActor* Actor : NearSample)
			{
				++Counts.FindOrAdd(Actor);
			}
		}

		const int32 MinimumSamples = FMath::Max(2, FMath::CeilToInt32(Samples.Num() * LineOverShare));
		TArray<FMarkedActor> Under;
		for (const TPair<AActor*, int32>& Count : Counts)
		{
			if (Count.Value >= MinimumSamples)
			{
				Under.Add({Count.Key, static_cast<double>(Count.Value) / Samples.Num(), Count.Value});
			}
		}

		SortByPixels(Under);
		if (Under.Num() > MaxLineActors)
		{
			Under.SetNum(MaxLineActors);
		}
		return Under;
	}

	AActor* GetActorAtDot(FViewport& Viewport, const FVector2D& Pixel)
	{
		const FIntPoint Center(FMath::RoundToInt32(Pixel.X), FMath::RoundToInt32(Pixel.Y));
		const FActorMap Map(Viewport, ToPixelRect(FBox2D(Pixel - DotReachPixels, Pixel + DotReachPixels), Viewport.GetSizeXY()));
		if (AActor* Actor = Map.At(Center.X, Center.Y))
		{
			return Actor;
		}

		TMap<AActor*, int32> Counts;
		AActor* Nearby = nullptr;
		int32 NearbyPixels = 0;
		for (AActor* Actor : Map.GetActors())
		{
			if (!Actor)
			{
				continue;
			}
			const int32 Pixels = ++Counts.FindOrAdd(Actor);
			if (Pixels > NearbyPixels)
			{
				NearbyPixels = Pixels;
				Nearby = Actor;
			}
		}
		return Nearby;
	}

	/** Turns viewport pixels into points in the level, where the camera's line of sight through them meets something. */
	class FLevelProjection
	{
	public:
		FLevelProjection(FSceneViewport& Viewport, FLevelEditorViewportClient& Client)
			: ViewFamily(FSceneViewFamily::ConstructionValues(&Viewport, Client.GetScene(), Client.EngineShowFlags).SetRealtimeUpdate(true))
			, View(Client.CalcSceneView(&ViewFamily))
			, World(Client.GetWorld())
		{
		}

		TOptional<FVector> Trace(const FVector2D& Pixel) const
		{
			if (!View || !World)
			{
				return {};
			}

			FVector Origin;
			FVector Direction;
			View->DeprojectFVector2D(Pixel, Origin, Direction);
			FHitResult Hit;
			const FCollisionQueryParams Params(SCENE_QUERY_STAT(CoPresenceAnnotate), true);
			if (!World->LineTraceSingleByChannel(Hit, Origin, Origin + Direction * TraceLength, ECC_Visibility, Params))
			{
				return {};
			}
			return Hit.ImpactPoint;
		}

	private:
		FSceneViewFamilyContext ViewFamily;
		FSceneView* View = nullptr;
		UWorld* World = nullptr;
	};

	TArray<TSharedPtr<FJsonValue>> ToJson(const FVector& Vector)
	{
		return {
			MakeShared<FJsonValueNumber>(FMath::RoundToDouble(Vector.X)),
			MakeShared<FJsonValueNumber>(FMath::RoundToDouble(Vector.Y)),
			MakeShared<FJsonValueNumber>(FMath::RoundToDouble(Vector.Z)),
		};
	}

	TSharedRef<FJsonObject> DescribeCamera(const FLevelEditorViewportClient& Client)
	{
		const FRotator Rotation = Client.GetViewRotation();
		const TSharedRef<FJsonObject> Camera = MakeShared<FJsonObject>();
		Camera->SetArrayField(TEXT("location"), ToJson(Client.GetViewLocation()));
		Camera->SetArrayField(TEXT("rotation"), ToJson(FVector(Rotation.Pitch, Rotation.Yaw, Rotation.Roll)));
		Camera->SetNumberField(TEXT("fov"), FMath::RoundToDouble(Client.ViewFOV));
		if (!Client.IsPerspective())
		{
			Camera->SetBoolField(TEXT("orthographic"), true);
		}
		return Camera;
	}

	TSharedRef<FJsonObject> DescribeActor(const AActor& Actor)
	{
		FVector Origin;
		FVector Extent;
		Actor.GetActorBounds(false, Origin, Extent);

		const TSharedRef<FJsonObject> Description = MakeShared<FJsonObject>();
		Description->SetStringField(TEXT("label"), Actor.GetActorLabel());
		Description->SetStringField(TEXT("path"), Actor.GetPathName());
		Description->SetStringField(TEXT("class"), Actor.GetClass()->GetName());
		Description->SetArrayField(TEXT("location"), ToJson(Actor.GetActorLocation()));
		Description->SetArrayField(TEXT("size"), ToJson(Extent * 2.0));
		const UStaticMeshComponent* MeshComponent = Actor.FindComponentByClass<UStaticMeshComponent>();
		if (MeshComponent && MeshComponent->GetStaticMesh())
		{
			Description->SetStringField(TEXT("mesh"), MeshComponent->GetStaticMesh()->GetPathName());
		}
		return Description;
	}

	TOptional<FViewportStroke> FindViewport(const TArray<FVector2D>& Points)
	{
		FLevelEditorModule* LevelEditorModule = FModuleManager::GetModulePtr<FLevelEditorModule>(TEXT("LevelEditor"));
		const TSharedPtr<ILevelEditor> LevelEditor = LevelEditorModule ? LevelEditorModule->GetLevelEditorInstance().Pin() : nullptr;
		if (!LevelEditor.IsValid())
		{
			return {};
		}

		for (const TSharedPtr<SLevelViewport>& LevelViewport : LevelEditor->GetViewports())
		{
			if (!LevelViewport.IsValid() || !LevelViewport->IsInForegroundTab())
			{
				continue;
			}
			const TSharedPtr<FSceneViewport> SceneViewport = LevelViewport->GetSceneViewport();
			if (!SceneViewport.IsValid())
			{
				continue;
			}

			const FGeometry& Geometry = SceneViewport->GetCachedGeometry();
			const FVector2D LocalSize = Geometry.GetLocalSize();
			const FIntPoint PixelSize = SceneViewport->GetSizeXY();
			if (LocalSize.X <= 0.0 || LocalSize.Y <= 0.0 || PixelSize.X <= 0 || PixelSize.Y <= 0)
			{
				continue;
			}

			FViewportStroke Stroke{LevelViewport, SceneViewport};
			int32 Inside = 0;
			for (const FVector2D& Point : Points)
			{
				const FVector2D Local = Geometry.AbsoluteToLocal(Point);
				if (Local.X >= 0.0 && Local.Y >= 0.0 && Local.X < LocalSize.X && Local.Y < LocalSize.Y)
				{
					++Inside;
				}
				Stroke.Pixels.Add(Local / LocalSize * FVector2D(PixelSize));
			}

			// A stroke that is mostly on the viewport is about the level
			if (Inside * 2 > Points.Num())
			{
				return Stroke;
			}
		}
		return {};
	}

	FString GetActorsLabel(const TArray<FMarkedActor>& Actors, const TCHAR* Prefix)
	{
		FString Label = Prefix + Actors[0].Actor->GetActorLabel();
		if (Actors.Num() > 1)
		{
			Label += FString::Printf(TEXT(" +%d"), Actors.Num() - 1);
		}
		return Label;
	}

	void ResolveInLevel(FCoPresenceAnnotationStroke& Stroke, EShape Shape, const FViewportStroke& ViewportStroke)
	{
		FSceneViewport& Viewport = *ViewportStroke.SceneViewport;
		FLevelEditorViewportClient& Client = ViewportStroke.LevelViewport->GetLevelViewportClient();
		const FLevelProjection Projection(Viewport, Client);
		const TArray<FVector2D>& Pixels = ViewportStroke.Pixels;

		const TSharedRef<FJsonObject> Mark = MakeShared<FJsonObject>();
		Mark->SetStringField(TEXT("on"), TEXT("level"));
		Mark->SetObjectField(TEXT("camera"), DescribeCamera(Client));

		TArray<FMarkedActor> Actors;
		AActor* MostInside = nullptr;
		TOptional<FVector> Point;
		switch (Shape)
		{
		case EShape::Dot:
			if (AActor* Actor = GetActorAtDot(Viewport, Pixels[0]))
			{
				Actors.Add({Actor});
			}
			Point = Projection.Trace(Pixels[0]);
			break;
		case EShape::Line:
			Actors = GetActorsUnderLine(Viewport, Pixels);
			break;
		case EShape::Loop:
			Actors = GetCircledActors(Viewport, Pixels, MostInside);
			Point = Projection.Trace(FBox2D(Pixels).GetCenter());
			break;
		}

		TArray<TSharedPtr<FJsonValue>> ActorValues;
		for (const FMarkedActor& Marked : Actors)
		{
			const TSharedRef<FJsonObject> Description = DescribeActor(*Marked.Actor);
			if (Shape != EShape::Dot)
			{
				Description->SetNumberField(TEXT("share"), FMath::RoundToDouble(Marked.Share * 100.0) / 100.0);
			}
			ActorValues.Add(MakeShared<FJsonValueObject>(Description));
		}
		Mark->SetArrayField(TEXT("actors"), ActorValues);

		if (Point.IsSet())
		{
			Mark->SetArrayField(TEXT("point"), ToJson(*Point));
		}

		// Where the stroke lies in the level, for marks like "along here" or "this area"
		if (Shape != EShape::Dot)
		{
			const double Spacing = GetLength(Pixels) / (MaxPathPoints - 1);
			TArray<TSharedPtr<FJsonValue>> PathValues;
			double DistanceSum = 0.0;
			for (const FVector2D& Sample : Resample(Pixels, Spacing))
			{
				if (const TOptional<FVector> PathPoint = Projection.Trace(Sample))
				{
					PathValues.Add(MakeShared<FJsonValueArray>(ToJson(*PathPoint)));
					DistanceSum += Point.IsSet() ? FVector::Distance(*Point, *PathPoint) : 0.0;
				}
			}
			Mark->SetArrayField(TEXT("path"), PathValues);

			// Only a loop around an area has a size in the level; around an actor, the top of the
			// loop lands on whatever is behind it
			if (Shape == EShape::Loop && Actors.IsEmpty() && Point.IsSet() && !PathValues.IsEmpty())
			{
				Mark->SetNumberField(TEXT("radius"), FMath::RoundToDouble(DistanceSum / PathValues.Num()));
			}
		}

		// A loop around nothing in particular marks a spot on whatever it is drawn over
		if (Shape == EShape::Loop && Actors.IsEmpty() && MostInside)
		{
			Mark->SetObjectField(TEXT("on_actor"), DescribeActor(*MostInside));
		}

		if (!Actors.IsEmpty())
		{
			Stroke.Label = GetActorsLabel(Actors, Shape == EShape::Line ? TEXT("over ") : TEXT(""));
		}
		else if (MostInside)
		{
			Stroke.Label = TEXT("a spot on ") + MostInside->GetActorLabel();
		}
		else
		{
			Stroke.Label = Shape == EShape::Line ? TEXT("a line") : TEXT("a spot");
		}
		Stroke.Mark = Mark;
	}

	struct FPanelText
	{
		FString Text;

		/** Where the text shows, in desktop space */
		FSlateRect Rect;

		/** The label of the tab it is in, or empty outside tabs */
		FString Tab;
	};

	/** Each open tab's content widget, with the tab's label. */
	void CollectTabs(const TSharedRef<SWidget>& Widget, TMap<const SWidget*, FString>& OutTabs)
	{
		if (Widget->GetType() == DockTabType)
		{
			const TSharedRef<SDockTab> Tab = StaticCastSharedRef<SDockTab>(Widget);
			OutTabs.Add(&Tab->GetContent().Get(), Tab->GetTabLabel().ToString());
		}

		FChildren* Children = Widget->GetChildren();
		for (int32 Index = 0; Index < Children->Num(); ++Index)
		{
			CollectTabs(Children->GetChildAt(Index), OutTabs);
		}
	}

	/** The text showing in the window: labels and the values of text fields. */
	void CollectTexts(const TSharedRef<SWidget>& Widget, const FString& Tab, const TMap<const SWidget*, FString>& Tabs, const SWidget* Overlay,
		TArray<FPanelText>& OutTexts)
	{
		if (&Widget.Get() == Overlay || !Widget->GetVisibility().IsVisible())
		{
			return;
		}

		const FString* ContentTab = Tabs.Find(&Widget.Get());
		const FString& CurrentTab = ContentTab ? *ContentTab : Tab;

		static const FName TextBlockType = STextBlock::StaticWidgetClass().GetWidgetType();
		static const FName EditableTextType = SEditableText::StaticWidgetClass().GetWidgetType();
		FString Text;
		if (Widget->GetType() == TextBlockType)
		{
			Text = StaticCastSharedRef<STextBlock>(Widget)->GetText().ToString();
		}
		else if (Widget->GetType() == EditableTextType)
		{
			Text = StaticCastSharedRef<SEditableText>(Widget)->GetText().ToString();
		}
		Text.TrimStartAndEndInline();
		if (!Text.IsEmpty())
		{
			const FSlateRect Rect = Widget->GetPaintSpaceGeometry().GetLayoutBoundingRect();
			if (Rect.GetArea() > 0.0)
			{
				OutTexts.Add({Text, Rect, CurrentTab});
			}
		}

		FChildren* Children = Widget->GetChildren();
		for (int32 Index = 0; Index < Children->Num(); ++Index)
		{
			CollectTexts(Children->GetChildAt(Index), CurrentTab, Tabs, Overlay, OutTexts);
		}
	}

	bool IsMarked(EShape Shape, const TArray<FVector2D>& Samples, const FSlateRect& Rect, double Scale)
	{
		if (Shape == EShape::Loop)
		{
			return IsInLoop(Samples, Rect.GetCenter());
		}

		const FSlateRect Reach = Rect.ExtendBy(FMargin(static_cast<float>(TextReach * Scale)));
		return Samples.ContainsByPredicate([&Reach](const FVector2D& Sample)
		{
			return Reach.ContainsPoint(Sample);
		});
	}

	/** The texts level with a marked one in the same tab, left to right, the way a Details or Outliner row reads. */
	FString GetRow(const FPanelText& Marked, const TArray<FPanelText>& Texts)
	{
		TArray<const FPanelText*> Row;
		for (const FPanelText& Text : Texts)
		{
			const double Middle = Text.Rect.GetCenter().Y;
			if (Text.Tab == Marked.Tab && Middle >= Marked.Rect.Top && Middle <= Marked.Rect.Bottom)
			{
				Row.Add(&Text);
			}
		}
		Row.Sort([](const FPanelText& First, const FPanelText& Second)
		{
			return First.Rect.Left < Second.Rect.Left;
		});

		TArray<FString> Parts;
		for (const FPanelText* Text : Row)
		{
			if (Parts.IsEmpty() || Parts.Last() != Text->Text)
			{
				Parts.Add(Text->Text);
			}
		}
		return FString::Join(Parts, TEXT(" | ")).Left(MaxRowLength);
	}

	/** The label of the tab under a point, or empty when it isn't on a tab. */
	FString GetTabAt(const FVector2D& Point, const TSharedRef<SWindow>& Window, const TMap<const SWidget*, FString>& Tabs)
	{
		const TArray<TSharedRef<SWindow>> Windows = {Window};
		const FWidgetPath Path = FSlateApplication::Get().LocateWindowUnderMouse(Point, Windows, true);
		FString Tab;
		for (int32 Index = 0; Index < Path.Widgets.Num(); ++Index)
		{
			if (const FString* Label = Tabs.Find(&Path.Widgets[Index].Widget.Get()))
			{
				Tab = *Label;
			}
		}
		return Tab;
	}

	void ResolveInPanels(FCoPresenceAnnotationStroke& Stroke, EShape Shape, const TSharedRef<SWindow>& Window, const SWidget* Overlay, double Scale)
	{
		TMap<const SWidget*, FString> Tabs;
		CollectTabs(Window, Tabs);
		TArray<FPanelText> Texts;
		CollectTexts(Window, FString(), Tabs, Overlay, Texts);

		const TArray<FVector2D> Samples = Shape == EShape::Loop ? Stroke.Points : Resample(Stroke.Points, LineSampleSpacing * Scale);
		const FVector2D Center = Shape == EShape::Line ? Samples[Samples.Num() / 2] : FBox2D(Stroke.Points).GetCenter();
		const FString Tab = GetTabAt(Center, Window, Tabs);

		TArray<FString> Rows;
		FString FirstText;
		for (const FPanelText& Text : Texts)
		{
			if (!IsMarked(Shape, Samples, Text.Rect, Scale))
			{
				continue;
			}
			if (FirstText.IsEmpty())
			{
				FirstText = Text.Text;
			}
			Rows.AddUnique(GetRow(Text, Texts));
			if (Rows.Num() >= MaxRows)
			{
				break;
			}
		}

		const TSharedRef<FJsonObject> Mark = MakeShared<FJsonObject>();
		Mark->SetStringField(TEXT("on"), TEXT("editor"));
		Mark->SetStringField(TEXT("tab"), Tab);
		TArray<TSharedPtr<FJsonValue>> RowValues;
		for (const FString& Row : Rows)
		{
			RowValues.Add(MakeShared<FJsonValueString>(Row));
		}
		Mark->SetArrayField(TEXT("rows"), RowValues);
		Stroke.Mark = Mark;

		if (Tab.IsEmpty())
		{
			Stroke.Label = FirstText.IsEmpty() ? TEXT("the editor") : FirstText;
		}
		else
		{
			Stroke.Label = FirstText.IsEmpty() ? Tab : Tab + TEXT(": ") + FirstText;
		}
	}
}

void UE::CoPresenceAnnotate::ResolveStroke(FCoPresenceAnnotationStroke& Stroke, const TSharedRef<SWindow>& Window, const SWidget* Overlay, double Scale)
{
	using namespace UE::CoPresenceAnnotate::Private;

	if (Stroke.Points.IsEmpty())
	{
		return;
	}

	const EShape Shape = GetShape(Stroke.Points, Scale);
	if (const TOptional<FViewportStroke> ViewportStroke = FindViewport(Stroke.Points))
	{
		ResolveInLevel(Stroke, Shape, *ViewportStroke);
	}
	else
	{
		ResolveInPanels(Stroke, Shape, Window, Overlay, Scale);
	}

	Stroke.Mark->SetStringField(TEXT("shape"), GetShapeName(Shape));
	Stroke.Mark->SetStringField(TEXT("label"), Stroke.Label);
	if (Stroke.Label.Len() > MaxLabelLength)
	{
		Stroke.Label = Stroke.Label.Left(MaxLabelLength - 3) + TEXT("...");
	}
}
