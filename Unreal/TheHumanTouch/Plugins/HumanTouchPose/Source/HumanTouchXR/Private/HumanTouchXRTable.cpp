#include "HumanTouchXRTable.h"

#include "Components/PrimitiveComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Containers/Ticker.h"
#include "Editor.h"
#include "Engine/StaticMeshActor.h"
#include "EngineUtils.h"
#include "HAL/IConsoleManager.h"
#include "IXRTrackingSystem.h"
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
FTSTicker::FDelegateHandle TickHandle;
IConsoleObject* AlignCommand = nullptr;
double CaptureAt = -1;
FString Message;
int32 LastMissing = INDEX_NONE;

FString StationLabel(int32 Number, const TCHAR* Base)
{
    return Number == 1 ? FString(Base) : FString::Printf(TEXT("MakerFleet_T%02d__%s"), Number - 1, Base);
}

int32 OwnerNumber(const FString& Label)
{
    // Fleet T00 is the sticker on the original station, whose geometry predates this prefix.
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
        FString Json, State;
        double Pid = 0, Updated = 0;
        TSharedPtr<FJsonObject> Report;
        const FString Path = FPaths::Combine(FPlatformProcess::UserHomeDir(), TEXT("Library/Application Support/OXRSys/runtime_status.json"));
        if (!FFileHelper::LoadFileToString(Json, *Path) ||
            !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Json), Report) || !Report.IsValid() ||
            !Report->TryGetStringField(TEXT("state"), State) || State != TEXT("streaming") ||
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
            const FString TableLabel = StationLabel(Number, TEXT("Dreamlabs_YAM_Table"));
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
        return true;
    UWorld* World = GEditor->GetEditorWorldContext().World();
    if (!IsStation(World))
        return true;
    if (!EyeView)
        EyeView = FSceneViewExtensions::NewExtension<FStationEyeView>();
    const int32 Number = HumanTouchXRTable::Number();
    const bool bCapture = CaptureAt >= 0 && FPlatformTime::Seconds() >= CaptureAt;
    if (!bCapture && PlacedAvatar.IsValid() && PlacedTable.IsValid() &&
        PlacedAvatar->GetWorld() == World &&
        PlacedTable->GetActorLabel(false) == StationLabel(Number, TEXT("Dreamlabs_YAM_Table")))
        return true;
    const UClass* AvatarClass = FindObject<UClass>(nullptr, TEXT("/Script/XRCreative.XRCreativeAvatar"));
    if (!AvatarClass)
        return true;
    const FString TableLabel = StationLabel(Number, TEXT("Dreamlabs_YAM_Table"));
    const FString FloorLabel = StationLabel(Number, TEXT("MakerEnvironment_Floor"));
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
    auto& XR = *GEngine->XRSystem;
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
    const FTransform TableToWorld = Mesh->GetComponentTransform();
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
        const FTransform Aligned = RoomAtNearEdge(Edge, Room.Z, Yaw, Head, HeadRotation.Rotator().Yaw);
        Room = Aligned.GetTranslation();
        Yaw = Aligned.Rotator().Yaw;
        Message = FString::Printf(TEXT("Table %d aligned to your near edge."), Number);
    }
    else
        Message = FString::Printf(TEXT("Table %d selected. Align at the middle of its near long edge."), Number);

    Avatar->SetActorLocationAndRotation(Room, FRotator(0, Yaw, 0), false, nullptr, ETeleportType::TeleportPhysics);
    PlacedAvatar = Avatar;
    PlacedTable = Table;
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
FString HumanTouchXRTable::Status() { return Message; }
void HumanTouchXRTable::AlignAfter(double Seconds)
{
    CaptureAt = FPlatformTime::Seconds() + Seconds;
    Message = Seconds > 0 ? TEXT("Aligning in five seconds. Look straight across the table.") : TEXT("Waiting for headset pose.");
}
void HumanTouchXRTable::Startup()
{
    int32 Number = 1;
    if (FParse::Value(FCommandLine::Get(), TEXT("HumanTouchTable="), Number))
        TableNumber->Set(Number, ECVF_SetByCommandline);
    AlignCommand = IConsoleManager::Get().RegisterConsoleCommand(TEXT("HumanTouch.AlignTable"),
        TEXT("Align the selected table using the current headset pose. Add 'delay' for five seconds."),
        FConsoleCommandWithArgsDelegate::CreateLambda([](const TArray<FString>& Args)
        {
            HumanTouchXRTable::AlignAfter(Args.Num() == 1 && Args[0] == TEXT("delay") ? 5.0 : 0.0);
        }));
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateStatic(&Tick), 0.25f);
}
void HumanTouchXRTable::Shutdown()
{
    FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
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
