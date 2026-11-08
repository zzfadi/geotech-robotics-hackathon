#include "HumanTouchCornerCapture.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/StaticMeshActor.h"
#include "EngineUtils.h"
#include "HeadMountedDisplayFunctionLibrary.h"
#include "IXRTrackingSystem.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"

using namespace HumanTouchCorners;

bool FHumanTouchCornerCapture::Geometry(UWorld *World, const FString &Prefix, TArray<FVector> &Out, FString &Error)
{
    Out.Reset();
    if (!World || !FMath::IsNearlyEqual(World->GetWorldSettings()->WorldToMeters, 100.f, 0.01f))
    {
        Error = TEXT("Set World to Meters to 100 before surveying.");
        return false;
    }
    // Look up instance transforms; replica positions and the interaction bay can change.
    TMap<FString, AStaticMeshActor *> Actors;
    for (TActorIterator<AStaticMeshActor> It(World); It; ++It)
    {
        const FString &Label = It->GetActorLabel(false);
        if (Label == Prefix + TEXT("Dreamlabs_YAM_Table") || Label == Prefix + TEXT("MakerEnvironment_Floor") ||
            Label.StartsWith(Prefix + TEXT("MakerArm_")))
        {
            if (Actors.Contains(Label))
            {
                Error = TEXT("Ambiguous station actor: ") + Label;
                return false;
            }
            Actors.Add(Label, *It);
        }
    }
    AStaticMeshActor *Table = Actors.FindRef(Prefix + TEXT("Dreamlabs_YAM_Table"));
    if (!Table)
    {
        Error = TEXT("Selected tabletop is missing.");
        return false;
    }
    auto *Mesh = Table->GetStaticMeshComponent();
    const FTransform Frame = Mesh->GetComponentTransform();
    FVector Lo, Hi;
    Mesh->GetLocalBounds(Lo, Hi);
    const FVector S = Frame.GetScale3D();
    if (S.GetMin() <= 0 || (Hi.X - Lo.X) * S.X < 10 || (Hi.Y - Lo.Y) * S.Y < 10 ||
        FVector::DotProduct(Frame.GetUnitAxis(EAxis::Z), FVector::UpVector) < 0.999)
    {
        Error = TEXT("Survey requires a level, upright tabletop larger than the marker insets.");
        return false;
    }

    for (const FVector &Local : {FVector(Lo.X, Lo.Y, Hi.Z), FVector(Lo.X, Hi.Y, Hi.Z), FVector(Hi.X, Hi.Y, Hi.Z),
                                 FVector(Hi.X, Lo.Y, Hi.Z)})
        Out.Add(Frame.TransformPosition(Local));
    for (const TCHAR *Side : {TEXT("left"), TEXT("right")})
    {
        const FString Arm = Prefix + TEXT("MakerArm_") + Side + TEXT("_");
        for (const TCHAR *Link :
             {TEXT("base_link"), TEXT("link_002"), TEXT("link_003"), TEXT("link_004"), TEXT("link_005"),
              TEXT("link_006"), TEXT("link_007"), TEXT("gripper_left"), TEXT("gripper_right")})
        {
            auto *Part = Actors.FindRef(Arm + Link);
            if (!Part || !Part->GetStaticMeshComponent()->GetComponentScale().Equals(FVector::OneVector, 0.001))
            {
                Error = TEXT("Missing or scaled Maker Arm link: ") + Arm + Link;
                return false;
            }
        }
        const FTransform Base = Actors[Arm + TEXT("base_link")]->GetStaticMeshComponent()->GetComponentTransform();
        // Pinned base_link part_012: outer holes are at CAD X +/-40, Z +/-90 mm.
        // Upper face is CAD Y=0, visual origin Y=-25.7 mm. OBJ import mirrors Y.
        for (const FVector &Hole :
             {FVector(4, 2.5700001, 9), FVector(4, 2.5700001, -9), FVector(-4, 2.5700001, 9)})
            Out.Add(Base.TransformPosition(Hole));
        const FTransform Wrist = Actors[Arm + TEXT("link_007")]->GetStaticMeshComponent()->GetComponentTransform();
        Out.Add(Wrist.TransformPosition(FVector(0, 0, -16.96387)));
    }
    auto *FloorActor = Actors.FindRef(Prefix + TEXT("MakerEnvironment_Floor"));
    if (!FloorActor)
    {
        Error = TEXT("Missing selected station floor.");
        return false;
    }
    FVector Center, Extents;
    FloorActor->GetActorBounds(false, Center, Extents);
    Out.Add(FVector(Out[0].X, Out[0].Y, Center.Z + Extents.Z));
    return true;
}

void FHumanTouchCornerCapture::Prompt()
{
    if (bPivot)
    {
        Notice = TEXT(
            "Seat the probe in a fixed dimple, click Capture, then pivot about two axes without lifting its tip.");
        return;
    }
    const int32 Index = Order[bCheck ? Count + Step : Step];
    const FString Instruction =
        Step >= Count || bCheck ? TEXT("Walk to a second viewpoint. Re-touch ") : TEXT("Touch ");
    Notice = Instruction + Name(Index) + TEXT(" with the ") +
             (ProbeTip.IsSet() ? TEXT("probe tip") : TEXT("right index tip")) + TEXT(", then capture.");
}
void FHumanTouchCornerCapture::Cancel(const FString &Reason)
{
    TableNumber = INDEX_NONE;
    StartAt = -1;
    Hold.Reset();
    Notice = Reason;
}
void FHumanTouchCornerCapture::Begin(int32 Table, const FString &Prefix, UWorld *World, bool bProbe)
{
    Cancel(TEXT(""));
    if (!Geometry(World, Prefix, Model, Notice))
        return;
    Points.Init(FVector::ZeroVector, Count);
    ProbeTip.Reset();
    PivotPoses.Reset();
    Step = 0;
    bCheck = false;
    bPivot = bProbe;
    CheckFailed = false;
    StationPrefix = Prefix;
    SurveyWorld = World;
    TableNumber = Table;
    Prompt();
}
void FHumanTouchCornerCapture::BeginCheck(int32 Table, const FString &Prefix, UWorld *World,
                                          const FTransform &Room)
{
    Begin(Table, Prefix, World, false);
    if (!IsRunning())
        return;
    bCheck = true;
    ProbeTip = AcceptedProbe;
    RoomToCheck = Room;
    Prompt();
}

void FHumanTouchCornerCapture::Next()
{
    if (!IsRunning() || StartAt >= 0)
        return;
    StartAt = FPlatformTime::Seconds() + 1;
    Hold.Reset();
    Notice = bPivot ? TEXT("Pivot the right controller about the fixed tip, varying pitch and roll.")
                    : FString::Printf(TEXT("Hold still on %s for one second."),
                                      Name(Order[bCheck ? Count + Step : Step]));
}

bool FHumanTouchCornerCapture::Tick(UWorld *World, int32 Table, bool bLive, FTransform &Room)
{
    if (!IsRunning())
        return false;
    if (!bLive || Table != TableNumber || World != SurveyWorld.Get())
    {
        Cancel(TEXT("Viewer, map or selected table changed. Restart the survey."));
        return false;
    }
    const double Now = FPlatformTime::Seconds();
    if (StartAt < 0 || Now < StartAt)
        return false;
    if (Now - StartAt > (bPivot ? 45. : 8.))
    {
        StartAt = -1;
        PivotPoses.Reset();
        Prompt();
        Notice += TEXT(" Tracking was missing or unsteady. Retry.");
        return false;
    }
    FVector Point;
    if (bPivot || ProbeTip.IsSet())
    {
        FXRMotionControllerState Grip;
        UHeadMountedDisplayFunctionLibrary::GetMotionControllerState(
            World, EXRSpaceType::XRTrackingSpace, EControllerHand::Right, EXRControllerPoseType::Grip, Grip);
        FString Profile;
        if (!Grip.bValid || Grip.TrackingStatus != ETrackingStatus::Tracked ||
            !GEngine->XRSystem->GetCurrentInteractionProfile(EControllerHand::Right, Profile) ||
            !Profile.Contains(TEXT("touch")) || Grip.ControllerLocation.ContainsNaN() ||
            Grip.ControllerRotation.ContainsNaN())
        {
            Hold.Reset();
            return false;
        }
        const FTransform Pose(Grip.ControllerRotation, Grip.ControllerLocation);
        if (bPivot)
        {
            for (const auto &Other : PivotPoses)
                if (Other.GetRotation().AngularDistance(Pose.GetRotation()) < FMath::DegreesToRadians(15.))
                    return false;
            PivotPoses.Add(Pose);
            Notice = FString::Printf(TEXT("Pivot orientations: %d/10. Keep the tip seated."), PivotPoses.Num());
            if (PivotPoses.Num() == 10)
            {
                StartAt = -1;
                FVector Tip;
                if (FitProbe(PivotPoses, Tip, Notice))
                {
                    ProbeTip = Tip;
                    bPivot = false;
                    Prompt();
                }
                PivotPoses.Reset();
            }
            return false;
        }
        Point = Pose.TransformPosition(ProbeTip.GetValue());
    }
    else
    {
        FXRHandTrackingState Hand;
        UHeadMountedDisplayFunctionLibrary::GetHandTrackingState(World, EXRSpaceType::UnrealWorldSpace,
                                                                 EControllerHand::Right, Hand);
        const int32 Tip = static_cast<int32>(EHandKeypoint::IndexTip);
        if (!Hand.bValid || Hand.TrackingStatus != ETrackingStatus::Tracked ||
            !Hand.HandKeyLocations.IsValidIndex(Tip))
        {
            Hold.Reset();
            return false;
        }
        Point =
            GEngine->XRSystem->GetTrackingToWorldTransform().InverseTransformPosition(Hand.HandKeyLocations[Tip]);
    }
    if (Point.ContainsNaN())
    {
        Hold.Reset();
        return false;
    }
    if (Now - LastSample > 0.6 ||
        Hold.ContainsByPredicate([&](const FVector &P) { return FVector::Dist(P, Point) > 0.4; }))
        Hold.Reset();
    LastSample = Now;
    Hold.Add(Point);
    if (Hold.Num() < 5)
        return false;
    FVector Mean = FVector::ZeroVector;
    for (const FVector &P : Hold)
        Mean += P;
    const FVector Captured = Mean / Hold.Num();
    StartAt = -1;
    const int32 Index = Order[bCheck ? Count + Step : Step];
    if (bCheck)
    {
        const double Miss = FVector::Dist(RoomToCheck.TransformPosition(Captured), Model[Index]);
        if (Miss > 0.5)
        {
            CheckFailed = true;
            Cancel(FString::Printf(TEXT("Drift check missed %s by %.1f mm. Registration withdrawn; resurvey."),
                                   Name(Index), Miss * 10));
        }
        else if (++Step == 2)
            Cancel(TEXT("Both independent mount checks passed within 5 mm."));
        else
            Prompt();
        return false;
    }
    if (Step < Count)
        Points[Index] = Captured;
    else if (FVector::Dist(Captured, Points[Index]) > 0.5)
    {
        Cancel(FString::Printf(TEXT("%s changed by more than 5 mm from the other viewpoint. Resurvey."),
                               Name(Index)));
        return false;
    }
    if (Step >= Count)
        Repeated[Step - Count] = Captured;
    ++Step;
    if (Step < Samples)
    {
        Prompt();
        return false;
    }
    TArray<FVector> Current;
    FString Problem;
    if (!Geometry(World, StationPrefix, Current, Problem))
    {
        Cancel(Problem);
        return false;
    }
    for (int32 I = 0; I < Count; ++I)
        if (!Model[I].Equals(Current[I], 0.1))
        {
            Cancel(TEXT("The table or parked robot moved during the survey. Restart."));
            return false;
        }
    FSolution Result;
    bool bPass = Solve(Points, Model, Result);
    for (int32 I = 0; bPass && I < 2; ++I)
    {
        const double Error = FVector::Dist(Result.Room.TransformPosition(Repeated[I]), Model[Order[Count + I]]);
        if (Error > 0.5)
        {
            Result.Problem = FString::Printf(TEXT("Second viewpoint missed %s by %.1f mm."),
                                             Name(Order[Count + I]), Error * 10);
            bPass = false;
        }
    }
    WriteReceipt(Table, Result);
    Cancel(bPass
               ? FString::Printf(TEXT("Verified table %d, both mounts and both parked tool points at 1:1."), Table)
               : Result.Problem);
    if (bPass)
    {
        Room = Result.Room;
        AcceptedProbe = ProbeTip;
    }
    return bPass;
}

void FHumanTouchCornerCapture::WriteReceipt(int32 Table, const FSolution &Result) const
{
    auto Report = MakeShared<FJsonObject>();
    Report->SetNumberField(TEXT("table"), Table);
    Report->SetStringField(TEXT("time_utc"), FDateTime::UtcNow().ToIso8601());
    Report->SetBoolField(TEXT("passed"), Result.Problem.IsEmpty());
    Report->SetStringField(TEXT("problem"), Result.Problem);
    Report->SetNumberField(TEXT("scale"), 1);
    Report->SetStringField(TEXT("input"),
                           ProbeTip.IsSet() ? TEXT("pivot_checked_probe") : TEXT("optical_index_tip"));
    Report->SetStringField(TEXT("fit_marks"), TEXT("L1,L3,R2,R3"));
    TArray<TSharedPtr<FJsonValue>> Repeats;
    for (int32 I = 0; I < 2; ++I)
    {
        const int32 Index = Order[Count + I];
        auto Row = MakeShared<FJsonObject>();
        Row->SetStringField(TEXT("mark"), Name(Index));
        Row->SetNumberField(TEXT("repeat_separation_cm"), FVector::Dist(Repeated[I], Points[Index]));
        if (Result.Errors.IsValidIndex(Index))
            Row->SetNumberField(TEXT("repeat_error_cm"),
                                FVector::Dist(Result.Room.TransformPosition(Repeated[I]), Model[Index]));
        Repeats.Add(MakeShared<FJsonValueObject>(Row));
    }
    Report->SetArrayField(TEXT("second_viewpoint"), Repeats);
    Report->SetStringField(TEXT("room_translation_cm"), Result.Room.GetTranslation().ToString());
    Report->SetNumberField(TEXT("room_yaw_deg"), Result.Room.Rotator().Yaw);
    Report->SetNumberField(TEXT("measured_table_length_cm"), Result.MeasuredLength);
    Report->SetNumberField(TEXT("measured_table_depth_cm"), Result.MeasuredDepth);
    const FVector Center = (Points[0] + Points[1] + Points[2] + Points[3]) * 0.25;
    Report->SetNumberField(TEXT("measured_table_height_cm"), Center.Z - Points[Floor].Z);
    if (Result.Errors.Num() == Count)
    {
        // For an author correcting a provisional station: keep the measured base frame
        // fixed and express the physical tabletop directly in UE editor coordinates.
        const FVector Top = Result.Room.TransformPosition(Center);
        Report->SetStringField(TEXT("surveyed_tabletop_center_world_cm"), Top.ToString());
        const FVector RightEdge = Result.Room.TransformVector(Points[1] - Points[0]).GetSafeNormal2D();
        Report->SetNumberField(TEXT("surveyed_table_forward_yaw_deg"),
                               FVector(RightEdge.Y, -RightEdge.X, 0).Rotation().Yaw);
        Report->SetNumberField(TEXT("surveyed_floor_world_z_cm"), Result.Room.TransformPosition(Points[Floor]).Z);
    }
    // Diagnostics remain useful when the modeled layout has the wrong size.
    // Physical surveyed flange centers in a near-left, tabletop-relative frame.
    const FVector Right = (Points[1] - Points[0]).GetSafeNormal2D();
    const FVector Across(Right.Y, -Right.X, 0);
    for (const int32 I : {4, 8})
    {
        const FVector Rel = (Points[I + 1] + Points[I + 2]) * 0.5 - Points[0];
        TArray<TSharedPtr<FJsonValue>> XYZ;
        for (const double Cm : {FVector::DotProduct(Rel, Across), FVector::DotProduct(Rel, Right), Rel.Z})
            XYZ.Add(MakeShared<FJsonValueNumber>(Cm));
        Report->SetArrayField(
            I == 4 ? TEXT("left_flange_center_from_A_cm") : TEXT("right_flange_center_from_A_cm"), XYZ);
    }
    TArray<TSharedPtr<FJsonValue>> Rows;
    for (int32 I = 0; I < Count; ++I)
    {
        auto Row = MakeShared<FJsonObject>();
        Row->SetStringField(TEXT("mark"), Name(I));
        Row->SetStringField(TEXT("tracking_cm"), Points[I].ToString());
        Row->SetStringField(TEXT("model_world_cm"), Model[I].ToString());
        if (Result.Errors.IsValidIndex(I))
        {
            Row->SetNumberField(TEXT("error_cm"), Result.Errors[I]);
            Row->SetStringField(TEXT("measured_minus_model_cm"),
                                (Result.Room.TransformPosition(Points[I]) - Model[I]).ToString());
        }
        Rows.Add(MakeShared<FJsonValueObject>(Row));
    }
    Report->SetArrayField(TEXT("marks"), Rows);
    FString Json;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Json));
    const FString Dir = FPaths::ProjectSavedDir() / TEXT("Capture/CornerSurveys");
    IFileManager::Get().MakeDirectory(*Dir, true);
    const FString Path = Dir / (FGuid::NewGuid().ToString() + TEXT(".json"));
    if (!FFileHelper::SaveStringToFile(Json, *Path))
        UE_LOG(LogTemp, Warning, TEXT("Could not write corner survey receipt: %s"), *Path);
}
