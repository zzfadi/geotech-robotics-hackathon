#include "HumanTouchXRTable.h"
#include "HumanTouchBench.h"
#include "HumanTouchRelock.h"
#include "HumanTouchCornerCapture.h"

#include "Components/PrimitiveComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Containers/Ticker.h"
#include "Editor.h"
#include "Engine/StaticMeshActor.h"
#include "EngineUtils.h"
#include "HAL/IConsoleManager.h"
#include "IXRTrackingSystem.h"
#include "Misc/CoreDelegates.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonSerializer.h"
#include "SceneInterface.h"
#include "SceneViewExtension.h"
#include "StereoRendering.h"
#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#endif

DEFINE_LOG_CATEGORY_STATIC(LogHumanTouchTable, Log, All);

namespace
{
TAutoConsoleVariable<int32> TableNumber(TEXT("HumanTouch.Table"), 1, TEXT("Printed table number used as the local XR origin."));
TAutoConsoleVariable<int32> HideOtherTables(TEXT("HumanTouch.HideOtherTables"), 1, TEXT("Hide the other numbered stations in stereo eye views."));
TWeakObjectPtr<AActor> PlacedAvatar;
TWeakObjectPtr<AStaticMeshActor> PlacedTable;
FTransform PlacedTableTransform;
TOptional<FTransform> MeasuredEdgePose;
EHMDTrackingOrigin::Type MeasuredOrigin = EHMDTrackingOrigin::LocalFloor;
FDelegateHandle RecenterHandle;
FTSTicker::FDelegateHandle TickHandle;
IConsoleObject* AlignCommand = nullptr;
double CaptureAt = -1;
FHumanTouchCornerCapture Survey;
TOptional<FTransform> VerifiedRoom;
TArray<FVector> VerifiedModel;
int32 VerifiedNumber = INDEX_NONE;
double NextGeometryCheck = 0;
IConsoleObject* SurveyCommand = nullptr;
FString Message;
int32 LastMissing = INDEX_NONE;
FHumanTouchRelock Optical;
FTSTicker::FDelegateHandle OpticalTicker;
IConsoleObject* OpticalCommand=nullptr;
IConsoleObject* DockCommand=nullptr;
int32 DockNumber=INDEX_NONE;
EHMDTrackingOrigin::Type DockOrigin=EHMDTrackingOrigin::LocalFloor;
bool bLiveOptical=false;
double NextLivePoll=0;

// Stereo-only copy owned by the local transient XR avatar. Shared scene actors stay at their authored transforms.
TWeakObjectPtr<UStaticMeshComponent> BenchVisual;
TWeakObjectPtr<UStaticMeshComponent> BenchSource;

void ClearBench()
{
    if (BenchVisual.IsValid()) BenchVisual->DestroyComponent();
    BenchVisual.Reset();
    BenchSource.Reset();
}

void UpdateBench(AActor* Avatar, UStaticMeshComponent* Source, const FTransform& Frame)
{
    if (!BenchVisual.IsValid() || BenchVisual->GetOwner() != Avatar || BenchSource.Get()!=Source ||
        BenchVisual->GetStaticMesh()!=Source->GetStaticMesh())
    {
        ClearBench();
        UStaticMeshComponent* Copy = NewObject<UStaticMeshComponent>(Avatar, NAME_None, RF_Transient | RF_TextExportTransient);
        Copy->SetMobility(EComponentMobility::Movable);
        Copy->SetStaticMesh(Source->GetStaticMesh());
        for (int32 I=0; I<Source->GetNumMaterials(); ++I) Copy->SetMaterial(I, Source->GetMaterial(I));
        Copy->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Copy->SetCanEverAffectNavigation(false);
        Copy->SetGenerateOverlapEvents(false);
        Copy->SetCastShadow(false);
        Copy->SetAbsolute(true,true,true);
        Copy->SetWorldTransform(Frame);
        Copy->RegisterComponent();
        BenchVisual=Copy;
        BenchSource=Source;
    }
    else if (!BenchVisual->GetComponentTransform().Equals(Frame)) BenchVisual->SetWorldTransform(Frame);
}

void ForgetRoom()
{
    ClearBench();
    Optical.Invalidate(); Optical.CancelDock();
    if (Survey.IsRunning() || VerifiedRoom.IsSet())
        Survey.Cancel(TEXT("Tracking origin changed. Repeat the corner survey."));
    VerifiedRoom.Reset();
    MeasuredEdgePose.Reset();
    PlacedAvatar.Reset();
    CaptureAt = -1;
}

FString Prefix(int32 Number)
{
    if (Number == 1) return TEXT("");
    if (Number == 10) return TEXT("MakerInteraction_T09__");
    return FString::Printf(TEXT("MakerFleet_T%02d__"), Number - 1);
}

FString StationLabel(int32 Number, const TCHAR* Base)
{
    return Prefix(Number) + Base;
}

// Earlier map revisions used the fleet prefix for the tenth station.
FString ScenePrefix(UWorld* World, int32 Number)
{
    if (Number != 10 || !World) return Prefix(Number);
    const FString Table = TEXT("Dreamlabs_YAM_Table");
    if (PlacedTable.IsValid() && PlacedTable->GetWorld() == World)
    {
        const FString& Label = PlacedTable->GetActorLabel(false);
        if (Label == Prefix(10)+Table || Label == TEXT("MakerFleet_T09__")+Table)
            return Label.LeftChop(Table.Len());
    }
    for (TActorIterator<AStaticMeshActor> It(World); It; ++It)
        if (It->GetActorLabel(false) == Prefix(10)+Table) return Prefix(10);
    return TEXT("MakerFleet_T09__");
}

int32 OwnerNumber(const FString& Label)
{
    // Fleet T00 is the sticker on the original station, whose geometry predates this prefix.
    if (Label.StartsWith(TEXT("MakerInteraction_T09__"))) return 10;
    if (Label.StartsWith(TEXT("MakerFleet_T")))
    {
        FString Index, Suffix;
        if (Label.Mid(12).Split(TEXT("__"), &Index, &Suffix) && Index.IsNumeric())
            return FCString::Atoi(*Index) + 1;
    }
    if (Label.StartsWith(TEXT("Dreamlabs_YAM_")) || Label.StartsWith(TEXT("MakerArm_")) ||
        Label.StartsWith(TEXT("MakerStation_")) || Label.StartsWith(TEXT("MakerCamera_")) ||
        Label.StartsWith(TEXT("MakerEnvironment_")))
        return 1;
    return INDEX_NONE;
}

FTransform RoomAtNearEdge(const FVector& Edge, double FloorZ, double TableYaw,
                          const FVector& TrackedHead, double TrackedYaw)
{
    const FRotator Rotation(0, TableYaw - TrackedYaw, 0);
    FVector Translation = Edge - Rotation.RotateVector(TrackedHead);
    Translation.Z = FloorZ;
    return FTransform(Rotation, Translation);
}

bool IsStation(UWorld* World)
{
    return World && !World->bIsTearingDown &&
        UWorld::RemovePIEPrefix(World->GetOutermost()->GetName()) == TEXT("/Game/Maps/TheHumanTouch");
}

bool HasLiveViewer()
{
#if PLATFORM_MAC
    // OXRSys retains its last head pose after the wireless viewer disconnects.
    if (!FPlatformMisc::GetEnvironmentVariable(TEXT("OXRSYS_TRANSPORT")).IsEmpty() ||
        FPlatformMisc::GetEnvironmentVariable(TEXT("XR_RUNTIME_JSON")).Contains(TEXT("oxrsys")))
    {
        FString Json, State, Device;
        double Pid = 0, Updated = 0;
        TSharedPtr<FJsonObject> Report;
        const FString Path = FPaths::Combine(FPlatformProcess::UserHomeDir(), TEXT("Library/Application Support/OXRSys/runtime_status.json"));
        if (!FFileHelper::LoadFileToString(Json, *Path) ||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Report) || !Report.IsValid() ||
            !Report->TryGetStringField(TEXT("state"), State) || State != TEXT("streaming") ||
            !Report->TryGetStringField(TEXT("device_type"), Device) || Device != TEXT("quest") ||
            !Report->TryGetNumberField(TEXT("process_id"), Pid) || Pid != FPlatformProcess::GetCurrentProcessId() ||
            !Report->TryGetNumberField(TEXT("updated_at_unix_ms"), Updated))
            return false;
        const double Age = FDateTime::UtcNow().ToUnixTimestampDecimal() - Updated / 1000.;
        return Age >= -1 && Age < 5;
    }
#endif
    return true;
}

class FStationEyeView : public FSceneViewExtensionBase
{
public:
    explicit FStationEyeView(const FAutoRegister& Register) : FSceneViewExtensionBase(Register) {}

    void SetupView(FSceneViewFamily& Family, FSceneView& View) override
    {
        if (BenchVisual.IsValid() && BenchSource.IsValid() && Family.Scene && Family.Scene->GetWorld()==BenchVisual->GetWorld())
        {
            const bool bLocalEye = IStereoRendering::IsStereoEyeView(View) && !View.bIsSceneCapture;
            View.HiddenPrimitives.Add((bLocalEye ? BenchSource.Get() : BenchVisual.Get())->GetPrimitiveSceneId());
        }
        if (!HumanTouchXRTable::IsHidingOthers() || View.bIsSceneCapture ||
            !IStereoRendering::IsStereoEyeView(View) || !Family.Scene)
            return;
        UWorld* World = Family.Scene->GetWorld();
        if (!IsStation(World))
            return;
        const double Now = FPlatformTime::Seconds();
        const int32 Number = HumanTouchXRTable::Number();
        if (CachedWorld.Get() != World || CachedNumber != Number || Now >= NextRefresh)
        {
            const FString TableLabel = ScenePrefix(World,Number) + TEXT("Dreamlabs_YAM_Table");
            bool bFoundTable = false;
            Hidden.Reset();
            for (TActorIterator<AActor> It(World); It; ++It)
            {
                const FString& Label = It->GetActorLabel(false);
                bFoundTable |= Label == TableLabel;
                const int32 Owner = OwnerNumber(Label);
                if (Owner == INDEX_NONE || Owner == Number)
                    continue;
                TInlineComponentArray<UPrimitiveComponent*> Components(*It);
                for (const UPrimitiveComponent* Component : Components)
                    Hidden.Add(Component->GetPrimitiveSceneId());
            }
            if (!bFoundTable)
                Hidden.Reset();
            if (CachedWorld.Get() != World || CachedNumber != Number)
                UE_LOG(LogHumanTouchTable, Display, TEXT("XR table %d: found=%d, %d other station primitives filtered in eye views"), Number, bFoundTable, Hidden.Num());
            CachedWorld = World;
            CachedNumber = Number;
            NextRefresh = Now + 0.5;
        }
        View.HiddenPrimitives.Append(Hidden);
    }
private:
    TWeakObjectPtr<UWorld> CachedWorld;
    int32 CachedNumber = INDEX_NONE;
    double NextRefresh = 0;
    TSet<FPrimitiveComponentId> Hidden;
};
TSharedPtr<FStationEyeView, ESPMode::ThreadSafe> EyeView;

bool SameFixedGeometry(const TArray<FVector> &Current)
{
    if (Current.Num() != VerifiedModel.Num())
        return false;
    for (int32 I = 0; I < Current.Num(); ++I)
    {
        // The two tool-center witnesses move when the arms articulate.
        if (I != 7 && I != 11 && !Current[I].Equals(VerifiedModel[I], .1))
            return false;
    }
    return true;
}

bool OpticalTick(float)
{
    if (!Optical.Enabled() && !Optical.DockPending())
        return true;
    if (!GEngine || !GEditor || !GEngine->XRSystem || !PlacedAvatar.IsValid() || Survey.IsRunning() || CaptureAt >= 0)
        return true;
    auto &XR = *GEngine->XRSystem;
    const double Now = FDateTime::UtcNow().ToUnixTimestampDecimal();
    const int32 Number = HumanTouchXRTable::Number();
    if (Now >= NextLivePoll)
    {
        bLiveOptical = HasLiveViewer();
        NextLivePoll = Now + .2;
    }
    if (Optical.DockPending() && (DockNumber != Number || DockOrigin != XR.GetTrackingOrigin()))
        Optical.CancelDock();
    if (!bLiveOptical || !XR.HasValidTrackingPosition() ||
        !PlacedAvatar->GetActorScale3D().Equals(FVector::OneVector, .001))
        return true;
    FQuat Q;
    FVector P;
    if (!XR.GetCurrentPose(IXRTrackingSystem::HMDDeviceId, Q, P) || Q.ContainsNaN() || P.ContainsNaN())
        return true;
    Optical.Sample(Now, FTransform(Q, P));
    FTransform Applied;
    FString Note;
    if (Optical.DockPending())
    {
        // Geometry is checked once per capture interval; no shared actors are modified.
        static double NextDockCheck = 0;
        if (Now < NextDockCheck)
            return true;
        NextDockCheck = Now + .20;
        TArray<FVector> Marks;
        UWorld *World = PlacedAvatar->GetWorld();
        if (!FHumanTouchCornerCapture::Geometry(World, ScenePrefix(World, Number), Marks, Note))
            Optical.CancelDock();
        else
        {
            const bool bCanTeach = VerifiedRoom.IsSet() && !Optical.DockSeed() && VerifiedNumber == Number &&
                                   MeasuredOrigin == XR.GetTrackingOrigin() && SameFixedGeometry(Marks) &&
                                   PlacedAvatar->GetActorTransform().Equals(VerifiedRoom.GetValue(), .02);
            if (Optical.DockTick(Now, Number, Marks, bCanTeach ? &VerifiedRoom.GetValue() : nullptr, Applied, Note))
            {
                VerifiedRoom = Applied;
                VerifiedNumber = Number;
                VerifiedModel = MoveTemp(Marks);
                MeasuredOrigin = XR.GetTrackingOrigin();
                MeasuredEdgePose.Reset();
                Optical.Seed(true);
                PlacedAvatar.Reset();
            }
        }
    }
    else if (VerifiedRoom.IsSet() && VerifiedNumber == Number && MeasuredOrigin == XR.GetTrackingOrigin() &&
             PlacedAvatar->GetActorTransform().Equals(VerifiedRoom.GetValue(), .02) &&
             Optical.Exchange(Now, Number, VerifiedModel, VerifiedRoom.GetValue(), Applied, Note))
    {
        TArray<FVector> Current;
        FString GeoError;
        const bool bRead = FHumanTouchCornerCapture::Geometry(
            PlacedAvatar->GetWorld(), ScenePrefix(PlacedAvatar->GetWorld(), Number), Current, GeoError);
        if (!bRead || !SameFixedGeometry(Current))
        {
            Optical.Invalidate();
            VerifiedRoom.Reset();
            Survey.Cancel(TEXT("Station changed. Holding the last alignment."));
            return true;
        }
        VerifiedRoom = Applied;
        PlacedAvatar->SetActorLocationAndRotation(Applied.GetLocation(), Applied.GetRotation(), false, nullptr,
                                                  ETeleportType::TeleportPhysics);
    }
    if (!Note.IsEmpty())
    {
        Survey.ClearNotice();
        Message = Note;
        UE_LOG(LogHumanTouchTable, Display, TEXT("%s"), *Note);
    }
    return true;
}

// Only the local XR Creative avatar owns the room transform. Its editor mode
// copies that transform to the stereo viewport every frame.
bool Tick(float)
{
    if (CaptureAt >= 0 && FPlatformTime::Seconds() - CaptureAt > 2)
    {
        CaptureAt = -1;
        Message = TEXT("Alignment expired. Open the XR station and try again.");
    }
    if (!GEditor || !GEngine || !GEngine->XRSystem || !GEngine->StereoRenderingDevice ||
        !GEngine->StereoRenderingDevice->IsStereoEnabled())
    {
        ForgetRoom();
        return true;
    }
    auto& XR = *GEngine->XRSystem;
    if ((MeasuredEdgePose.IsSet() || Survey.IsRunning() || VerifiedRoom.IsSet()) && XR.GetTrackingOrigin() != MeasuredOrigin)
        ForgetRoom();
    UWorld* World = GEditor->GetEditorWorldContext().World();
    if (!IsStation(World))
    {
        ForgetRoom();
        return true;
    }
    if (!EyeView)
        EyeView = FSceneViewExtensions::NewExtension<FStationEyeView>();
    const int32 Number = HumanTouchXRTable::Number();
    if (Survey.IsRunning())
    {
        FTransform Solved;
        const bool bSameAvatar = PlacedAvatar.IsValid() && PlacedAvatar->GetWorld() == World &&
            PlacedAvatar->GetActorScale3D().Equals(FVector::OneVector, 0.001);
        if (Survey.Tick(World, Number, bSameAvatar && XR.HasValidTrackingPosition() && HasLiveViewer(), Solved))
        {
            VerifiedRoom = Solved;
            Optical.Seed(false);
            VerifiedNumber = Number;
            VerifiedModel = Survey.Targets();
            MeasuredEdgePose.Reset();
            PlacedAvatar.Reset();
        }
    }
    if (Survey.CheckFailed)
    {
        VerifiedRoom.Reset();
        Optical.Invalidate();
        Survey.CheckFailed = false;
    }
    if (VerifiedRoom.IsSet() && (Number != VerifiedNumber || FPlatformTime::Seconds() >= NextGeometryCheck))
    {
        NextGeometryCheck = FPlatformTime::Seconds() + 1;
        TArray<FVector> Current;
        FString Error;
        const bool bMatches = Number == VerifiedNumber && XR.HasValidTrackingPosition() && HasLiveViewer() &&
            FHumanTouchCornerCapture::Geometry(World, ScenePrefix(World,Number), Current, Error) && SameFixedGeometry(Current);
        if (!bMatches)
        {
            VerifiedRoom.Reset();
            Optical.Invalidate();
            if (Number != VerifiedNumber) PlacedAvatar.Reset();
            Survey.Cancel(TEXT("Survey expired after a station or viewer change. Repeat the corner survey."));
        }
    }
    const bool bCapture = CaptureAt >= 0 && FPlatformTime::Seconds() >= CaptureAt;
    if (!bCapture && PlacedAvatar.IsValid() && BenchVisual.IsValid() && PlacedTable.IsValid() &&
        PlacedAvatar->GetWorld() == World &&
        PlacedTable->GetActorLabel(false) == ScenePrefix(World,Number) + TEXT("Dreamlabs_YAM_Table") &&
        PlacedTable->GetActorTransform().Equals(PlacedTableTransform))
        return true;
    const UClass* AvatarClass = FindObject<UClass>(nullptr, TEXT("/Script/XRCreative.XRCreativeAvatar"));
    if (!AvatarClass)
        return true;
    const FString TableLabel = ScenePrefix(World,Number) + TEXT("Dreamlabs_YAM_Table");
    const FString FloorLabel = ScenePrefix(World,Number) + TEXT("MakerEnvironment_Floor");
    AActor* Avatar = nullptr;
    AStaticMeshActor* Table = nullptr;
    AActor* Floor = nullptr;
    for (TActorIterator<AActor> It(World); It; ++It)
    {
        const FString& Label = It->GetActorLabel(false);
        if (It->IsA(AvatarClass)) Avatar = *It;
        else if (Label == TableLabel) Table = Cast<AStaticMeshActor>(*It);
        else if (Label == FloorLabel) Floor = *It;
    }
    if (!Avatar)
        return true;
    if (!Table || !Floor)
    {
        CaptureAt = -1;
        if (LastMissing != Number)
        {
            Message = FString::Printf(TEXT("Table %d or its floor is missing from this map."), Number);
            UE_LOG(LogHumanTouchTable, Warning, TEXT("%s"), *Message);
            LastMissing = Number;
        }
        return true;
    }
    LastMissing = INDEX_NONE;
    const auto Origin = XR.GetTrackingOrigin();
    if (Origin != EHMDTrackingOrigin::LocalFloor && Origin != EHMDTrackingOrigin::Stage)
    {
        XR.SetTrackingOrigin(EHMDTrackingOrigin::LocalFloor);
        if (XR.GetTrackingOrigin() != EHMDTrackingOrigin::LocalFloor)
        {
            Message = TEXT("A floor-level tracking origin is required.");
            CaptureAt = -1;
            return true;
        }
    }
    UStaticMeshComponent* Mesh = Table->GetStaticMeshComponent();
    FVector Min, Max;
    Mesh->GetLocalBounds(Min, Max);
    const FTransform TableToWorld = HumanTouchBench::Tabletop(Mesh->GetComponentTransform(),Min,Max);
    UpdateBench(Avatar, Mesh, TableToWorld);
    const FVector Center = (Min + Max) * 0.5;
    // The stock tabletop's local +X points across the short dimension.
    const FVector Edge = TableToWorld.TransformPosition(FVector(Min.X, Center.Y, Max.Z));
    const FVector Across = TableToWorld.TransformVector(FVector::ForwardVector).GetSafeNormal2D();
    FVector Room = TableToWorld.TransformPosition(Center);
    double Yaw = Across.Rotation().Yaw;
    FVector FloorCenter, FloorExtent;
    Floor->GetActorBounds(false, FloorCenter, FloorExtent);
    Room.Z = FloorCenter.Z + FloorExtent.Z;
    if (bCapture)
    {
        CaptureAt = -1;
        if (!HasLiveViewer())
        {
            Message = TEXT("Open OXRSys in the headset and wait for the live scene before aligning.");
            UE_LOG(LogHumanTouchTable, Warning, TEXT("%s"), *Message);
            return true;
        }
        FQuat HeadRotation;
        FVector Head;
        if (!XR.HasValidTrackingPosition() || !XR.GetCurrentPose(IXRTrackingSystem::HMDDeviceId, HeadRotation, Head) ||
            HeadRotation.GetForwardVector().SizeSquared2D() < 0.25 || Head.ContainsNaN() || HeadRotation.ContainsNaN())
        {
            Message = TEXT("Look straight across the table with headset tracking active, then try Align again.");
            UE_LOG(LogHumanTouchTable, Warning, TEXT("%s"), *Message);
            return true;
        }
        VerifiedRoom.Reset();
        Optical.Invalidate();
        Survey.ClearNotice();
        MeasuredEdgePose.Emplace(HeadRotation, Head);
        MeasuredOrigin = XR.GetTrackingOrigin();
    }
    // Concert can replace the map and avatar at checkpoints. The measured point
    // belongs to tracking space and can be reused with the new table geometry.
    if (VerifiedRoom.IsSet())
    {
        Room = VerifiedRoom->GetTranslation();
        Yaw = VerifiedRoom->Rotator().Yaw;
        Message = FString::Printf(TEXT("Table %d %s registration at unit scale."), Number, Optical.DockSeed()?TEXT("static dock"):TEXT("factory-screw"));
    }
    else if (MeasuredEdgePose.IsSet())
    {
        const FTransform Aligned = RoomAtNearEdge(Edge, Room.Z, Yaw, MeasuredEdgePose->GetTranslation(),
                                                  MeasuredEdgePose->Rotator().Yaw);
        Room = Aligned.GetTranslation();
        Yaw = Aligned.Rotator().Yaw;
        Message = FString::Printf(TEXT("Table %d aligned to your near edge."), Number);
    }
    else
        Message = FString::Printf(TEXT("Table %d selected. Rest the headset at the center of the long edge opposite the arms."), Number);

    Avatar->SetActorLocationAndRotation(Room, FRotator(0, Yaw, 0), false, nullptr, ETeleportType::TeleportPhysics);
    PlacedAvatar = Avatar;
    PlacedTable = Table;
    PlacedTableTransform = Table->GetActorTransform();
    UE_LOG(LogHumanTouchTable, Display, TEXT("%s Room origin %s, yaw %.2f"), *Message, *Room.ToString(), Yaw);
    return true;
}
}

int32 HumanTouchXRTable::Number()
{
    const int32 Requested = TableNumber.GetValueOnGameThread();
    return Requested == 0 ? 1 : Requested;
}
bool HumanTouchXRTable::IsHidingOthers() { return HideOtherTables.GetValueOnGameThread() != 0; }
void HumanTouchXRTable::HideOthers(bool bHide) { HideOtherTables->Set(bHide ? 1 : 0, ECVF_SetByConsole); }
FString HumanTouchXRTable::Status() { return Survey.Status().IsEmpty() ? Message : Survey.Status(); }
bool HumanTouchXRTable::SurveyRunning() { return Survey.IsRunning(); }
bool HumanTouchXRTable::CanCheckSurvey() { return VerifiedRoom.IsSet() && !Optical.DockSeed() && !Survey.IsRunning(); }
void HumanTouchXRTable::CheckSurvey()
{
    if (CanCheckSurvey() && GEditor)
    {
        UWorld* World = GEditor->GetEditorWorldContext().World();
        Survey.BeginCheck(Number(), ScenePrefix(World,Number()), World, VerifiedRoom.GetValue());
    }
}
void HumanTouchXRTable::CancelSurvey() { Survey.Cancel(TEXT("Survey cancelled.")); }
void HumanTouchXRTable::CaptureMark() { Survey.Next(); }
void HumanTouchXRTable::BeginSurvey(bool bProbe)
{
    UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
    if (!IsStation(World) || !GEngine || !GEngine->XRSystem || !GEngine->StereoRenderingDevice ||
        !GEngine->StereoRenderingDevice->IsStereoEnabled() || !HasLiveViewer() ||
        !PlacedAvatar.IsValid() || PlacedAvatar->GetWorld() != World ||
        !PlacedAvatar->GetActorScale3D().Equals(FVector::OneVector, 0.001))
    {
        Survey.Cancel(TEXT("Enter station XR with the live headset before starting a corner survey."));
        return;
    }
    MeasuredOrigin = GEngine->XRSystem->GetTrackingOrigin();
    if (MeasuredOrigin != EHMDTrackingOrigin::LocalFloor && MeasuredOrigin != EHMDTrackingOrigin::Stage)
    {
        Survey.Cancel(TEXT("Select a floor-level tracking origin before surveying."));
        return;
    }
    CaptureAt = -1;
    Survey.Begin(Number(), ScenePrefix(World,Number()), World, bProbe);
}
void HumanTouchXRTable::Dock(const TArray<FString>& Args)
{
    if (!GEngine || !GEngine->XRSystem || !PlacedAvatar.IsValid() || !HasLiveViewer() || Survey.IsRunning())
    {
        UE_LOG(LogHumanTouchTable, Warning, TEXT("Dock needs the live XR station with no active probe survey"));
        return;
    }
    DockNumber = HumanTouchXRTable::Number();
    DockOrigin = GEngine->XRSystem->GetTrackingOrigin();
    if (DockOrigin != EHMDTrackingOrigin::LocalFloor && DockOrigin != EHMDTrackingOrigin::Stage)
    {
        UE_LOG(LogHumanTouchTable, Warning, TEXT("Select a floor-level tracking origin before docking."));
        return;
    }
    Survey.ClearNotice();
    CaptureAt = -1;
    Optical.DockRequest(Args, Message);
    UE_LOG(LogHumanTouchTable, Display, TEXT("%s"), *Message);
}
void HumanTouchXRTable::AlignAfter(double Seconds)
{
    if (Survey.IsRunning()) CancelSurvey();
    Survey.ClearNotice();
    CaptureAt = FPlatformTime::Seconds() + Seconds;
    Message = Seconds > 0 ? TEXT("Aligning in five seconds. Look straight across the table.") : TEXT("Waiting for headset pose.");
}
void HumanTouchXRTable::Startup()
{
    int32 Number = 1;
    if (FParse::Value(FCommandLine::Get(), TEXT("HumanTouchTable="), Number))
        TableNumber->Set(Number, ECVF_SetByCommandline);
    RecenterHandle = FCoreDelegates::VRHeadsetRecenter.AddStatic(&ForgetRoom);
    AlignCommand = IConsoleManager::Get().RegisterConsoleCommand(TEXT("HumanTouch.AlignTable"),
        TEXT("Align the selected table using the current headset pose. Add 'delay' for five seconds."),
        FConsoleCommandWithArgsDelegate::CreateLambda([](const TArray<FString>& Args)
        {
            HumanTouchXRTable::AlignAfter(Args.Num() == 1 && Args[0] == TEXT("delay") ? 5.0 : 0.0);
        }));
    SurveyCommand = IConsoleManager::Get().RegisterConsoleCommand(TEXT("HumanTouch.Survey"),
        TEXT("HumanTouch.Survey start|hand|capture|check|cancel|layout|status"), FConsoleCommandWithArgsDelegate::CreateLambda(
        [](const TArray<FString>& Args)
        {
            if (Args.Num()==1 && Args[0]==TEXT("layout") && GEditor)
            {
                TArray<FVector> Marks;
                FString Error;
                auto* World = GEditor->GetEditorWorldContext().World();
                if (FHumanTouchCornerCapture::Geometry(World,ScenePrefix(World,HumanTouchXRTable::Number()),Marks,Error))
                {
                    UE_LOG(LogHumanTouchTable,Display,TEXT("Survey layout: table %d, %.1f x %.1f cm, surface %.1f cm above floor, L1/R1 %.1f/%.1f cm above tabletop"),
                        HumanTouchXRTable::Number(), FVector::Dist(Marks[0],Marks[1]),FVector::Dist(Marks[1],Marks[2]),
                        Marks[0].Z-Marks[HumanTouchCorners::Floor].Z,Marks[4].Z-Marks[0].Z,Marks[8].Z-Marks[0].Z);
                    for (int32 I=0; I<Marks.Num(); ++I)
                        UE_LOG(LogHumanTouchTable,Display,TEXT("  %s: %s"),HumanTouchCorners::Name(I),*Marks[I].ToString());
                }
                else UE_LOG(LogHumanTouchTable,Warning,TEXT("%s"),*Error);
                return;
            }
            if (Args.Num()==1 && Args[0]==TEXT("start")) HumanTouchXRTable::BeginSurvey();
            if (Args.Num()==1 && Args[0]==TEXT("hand")) HumanTouchXRTable::BeginSurvey(false);
            if (Args.Num()==1 && Args[0]==TEXT("check")) HumanTouchXRTable::CheckSurvey();
            if (Args.Num()==1 && Args[0]==TEXT("capture")) HumanTouchXRTable::CaptureMark();
            if (Args.Num()==1 && Args[0]==TEXT("cancel")) HumanTouchXRTable::CancelSurvey();
            UE_LOG(LogHumanTouchTable,Display,TEXT("%s"),*HumanTouchXRTable::Status());
        }));
    const bool bLaunchRelock = FParse::Param(FCommandLine::Get(), TEXT("HumanTouchDriftCorrection"));
    Optical.Enable(bLaunchRelock, bLaunchRelock);
    OpticalCommand = IConsoleManager::Get().RegisterConsoleCommand(
        TEXT("HumanTouch.Relock"), TEXT("HumanTouch.Relock on (observe) | apply | off"),
        FConsoleCommandWithArgsDelegate::CreateLambda([](const TArray<FString> &Args) {
            if (Args.Num() == 1 && (Args[0] == TEXT("on") || Args[0] == TEXT("off") || Args[0]==TEXT("apply")))
                Optical.Enable(Args[0] != TEXT("off"),Args[0]==TEXT("apply"));
            UE_LOG(LogHumanTouchTable, Display, TEXT("Look-to-relock %s"),
                   Optical.Writable() ? TEXT("corrections enabled") : Optical.Enabled() ? TEXT("observation only") : TEXT("off"));
        }));
    DockCommand = IConsoleManager::Get().RegisterConsoleCommand(
        TEXT("HumanTouch.Dock"),
        TEXT("HumanTouch.Dock [edge|midpoint] learn NAME | snap NAME | offset forward_cm right_cm up_cm yaw_deg | cancel"),
        FConsoleCommandWithArgsDelegate::CreateStatic(&HumanTouchXRTable::Dock));
    OpticalTicker = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateStatic(&OpticalTick));
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateStatic(&Tick), 0.25f);
}
void HumanTouchXRTable::Shutdown()
{
    ClearBench();
    Optical.Enable(false);
    FTSTicker::GetCoreTicker().RemoveTicker(OpticalTicker);
    if(OpticalCommand) IConsoleManager::Get().UnregisterConsoleObject(OpticalCommand);
    if(DockCommand) IConsoleManager::Get().UnregisterConsoleObject(DockCommand);
    FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
    FCoreDelegates::VRHeadsetRecenter.Remove(RecenterHandle);
    if (SurveyCommand) IConsoleManager::Get().UnregisterConsoleObject(SurveyCommand);
    if (AlignCommand) IConsoleManager::Get().UnregisterConsoleObject(AlignCommand);
    EyeView.Reset();
}

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FHumanTouchXRTableTest, "HumanTouch.XR.TableAlignment",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FHumanTouchXRTableTest::RunTest(const FString&)
{
    TestEqual(TEXT("Unprefixed tabletop"), OwnerNumber(TEXT("Dreamlabs_YAM_Table")), 1);
    TestEqual(TEXT("First number sticker"), OwnerNumber(TEXT("MakerFleet_T00__NumberText")), 1);
    TestEqual(TEXT("Replica arm"), OwnerNumber(TEXT("MakerFleet_T03__MakerArm_right_link_004")), 4);
    TestEqual(TEXT("Shared avatar"), OwnerNumber(TEXT("XRCreativeAvatar")), INDEX_NONE);
    TestEqual(TEXT("Interaction bay has its own prefix"), OwnerNumber(TEXT("MakerInteraction_T09__MakerArm_left_base_link")), 10);
    TestEqual(TEXT("Table 10 physical bay"), StationLabel(10, TEXT("Dreamlabs_YAM_Table")),
              FString(TEXT("MakerInteraction_T09__Dreamlabs_YAM_Table")));
    TestEqual(TEXT("Printed number maps to replica label"), StationLabel(4, TEXT("Dreamlabs_YAM_Table")),
              FString(TEXT("MakerFleet_T03__Dreamlabs_YAM_Table")));
    const FVector Head(53, -29, 162), Edge(825, 820, 75);
    const FTransform Room = RoomAtNearEdge(Edge, 3, -20, Head, 117);
    const FVector PlacedHead = Room.TransformPosition(Head);
    TestTrue(TEXT("Head projects onto the selected edge while retaining its tracked height"),
             PlacedHead.Equals(FVector(Edge.X, Edge.Y, 165), 0.001));
    TestTrue(TEXT("Horizontal gaze points across the selected tabletop"),
        Room.TransformVector(FRotator(0,117,0).Vector()).Equals(FRotator(0,-20,0).Vector(), 0.001));
    return true;
}
#endif
