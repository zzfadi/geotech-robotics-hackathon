#include "AnimPreviewAttacheInstance.h"
#include "HumanTouchPerformerMode.h"
#include "HumanTouchFootPlacement.h"
#include "RetargetEditor/IKRetargeterController.h"
#include "HumanTouchPerformerWidget.h"
#include "Blueprint/UserWidget.h"
#include "ChaosClothAsset/ClothComponent.h"
#include "ChaosOutfitAsset/OutfitAsset.h"
#include "Retargeter/RetargetOps/PelvisMotionOp.h"
#include "Components/SkinnedMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Animation/SkeletalMeshActor.h"
#include "GroomComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "HAL/IConsoleManager.h"
#include "RetargetComponent.h"
#include "Retargeter/IKRetargeter.h"
#include "ToolMenus.h"
#include "Framework/Docking/TabManager.h"
#include "Widgets/Docking/SDockTab.h"
#include "Components/SkeletalMeshComponent.h"
#include "Containers/Ticker.h"
#include "Editor.h"
#include "Engine/World.h"
#include "HumanTouchRetarget.h"
#include "MetaHumanCharacter.h"
#include "MetaHumanCharacterEditorActorInterface.h"
#include "MetaHumanCharacterEditorSubsystem.h"
#include "Modules/ModuleManager.h"
#include "PreviewScene.h"
#include "UObject/StrongObjectPtr.h"

DEFINE_LOG_CATEGORY_STATIC(LogHumanTouchPerformers, Log, All);

namespace
{
constexpr TCHAR TemplateMount[] = TEXT("/Game/Characters/Mannequins/");
const FName TabId(TEXT("HumanTouchPerformerSettings"));
EHumanTouchCast SelectedCast = EHumanTouchCast::Mannequins;

struct FPerformer
{
    const TCHAR *Preset;
    const TCHAR *Subject;
    FVector Location;
    float Yaw;
    FLinearColor Shirt;
    TStrongObjectPtr<UMetaHumanCharacter> Character;
    bool Attempted = false;
    TStrongObjectPtr<USkeletalMesh> Garment;
    TStrongObjectPtr<UIKRetargeter> LiveRig;
};

bool IsStation(UWorld *World)
{
    if (!World || !World->PersistentLevel || World->bIsTearingDown)
        return false;
    const FString Map = UWorld::RemovePIEPrefix(World->GetOutermost()->GetName());
    return Map == TEXT("/Game/Maps/TheHumanTouch");
}

UMetaHumanCharacter *PrepareCharacter(FPerformer &Performer, UMetaHumanCharacterEditorSubsystem *Creator)
{
    if (Performer.Attempted)
        return Performer.Character.Get();
    Performer.Attempted = true;
    const FString Path =
        FString::Printf(TEXT("/MetaHumanCharacter/Optional/Presets/%s.%s"), Performer.Preset, Performer.Preset);
    const auto *Preset = LoadObject<UMetaHumanCharacter>(nullptr, *Path);
    if (Preset)
    {
        // Own the generated meshes and wardrobe in this process. Never edit Epic's preset asset.
        TStrongObjectPtr<UMetaHumanCharacter> Copy(
            DuplicateObject<UMetaHumanCharacter>(Preset, GetTransientPackage()));
        Copy->ClearFlags(RF_Public | RF_Standalone | RF_Transactional);
        Copy->SetFlags(RF_Transient);
        if (Creator->TryAddObjectToEdit(Copy.Get()))
        {
            Creator->RunCharacterEditorPipelineForPreview(Copy.Get());
            Performer.Character = MoveTemp(Copy);
            return Performer.Character.Get();
        }
    }
    UE_LOG(LogHumanTouchPerformers, Error, TEXT("Could not initialize %s. Check MetaHuman Creator Core Data."),
           *Path);
    return nullptr;
}
void DressForXR(AActor &Actor, USkeletalMeshComponent *Body, FPerformer &Performer)
{
    TInlineComponentArray<UChaosClothComponent *> Outfits(&Actor);
    if (Outfits.IsEmpty())
        UE_LOG(LogHumanTouchPerformers, Error, TEXT("%s has no assembled outfit"), *Actor.GetActorLabel());
    for (auto *Outfit : Outfits)
    {
        auto *PreviewOutfit = Cast<UChaosOutfitAsset>(Outfit->GetAsset());
        if (!Performer.Garment.IsValid() && PreviewOutfit && PreviewOutfit->GetDataflow())
        {
            // Creator skips skin transfer for unrigged preview faces. Re-evaluate the stock
            // fitting graph against the same body, then use Epic's production skeletal export.
            TStrongObjectPtr<UChaosOutfitAsset> Fitted(
                NewObject<UChaosOutfitAsset>(GetTransientPackage(), NAME_None, RF_Transient));
            Fitted->SetDataflow(PreviewOutfit->GetDataflow());
            auto &Graph = Fitted->GetDataflowInstance();
            auto &Variables = Graph.GetVariableOverrides();
            Variables = PreviewOutfit->GetDataflowInstance().GetVariableOverrides();
            Variables.OverrideVariableBool(TEXT("TransferSkinWeights"), true);
            Variables.OverrideVariableBool(TEXT("GenerateLOD0Only"), false);
            Variables.OverrideVariableBool(TEXT("StripSimMesh"), true);
            Graph.UpdateOwnerAsset(true);
            TStrongObjectPtr<USkeletalMesh> Mesh(
                NewObject<USkeletalMesh>(GetTransientPackage(), NAME_None, RF_Transient));
            if (Fitted->ExportToSkeletalMesh(*Mesh))
            {
                Mesh->SetSkeleton(Body->GetSkeletalMeshAsset()->GetSkeleton());
                Performer.Garment = MoveTemp(Mesh);
            }
        }
        if (!Performer.Garment.IsValid())
        {
            UE_LOG(LogHumanTouchPerformers, Error, TEXT("Could not skin the fitted outfit for %s"),
                   Performer.Preset);
            continue;
        }
        auto *Clothes = NewObject<USkeletalMeshComponent>(&Actor, TEXT("PerformanceOutfit"), RF_Transient);
        Actor.AddInstanceComponent(Clothes);
        Clothes->SetupAttachment(Body);
        Clothes->SetSkeletalMesh(Performer.Garment.Get());
        Clothes->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Clothes->RegisterComponent();
        Clothes->SetLeaderPoseComponent(Body);
        Clothes->SetForcedLOD(FMath::Clamp(3, 1, Clothes->GetNumLODs()));
        Outfit->SetEnableSimulation(false);
        Outfit->SetVisibility(false);
        Outfit->SetHiddenInGame(true);
        static_cast<UActorComponent *>(Outfit)->SetComponentTickEnabled(false);
        const auto Slots = Clothes->GetMaterialSlotNames();
        for (int32 Slot = 0; Slot < Slots.Num(); ++Slot)
        {
            const bool Top = Slots[Slot].ToString().Contains(TEXT("Shirt"));
            const FLinearColor Color = Top ? Performer.Shirt : FLinearColor(.035f, .055f, .085f);
            if (auto *Fabric = Clothes->CreateDynamicMaterialInstance(
                    Slot, Outfit->GetMaterial(Outfit->GetMaterialIndex(Slots[Slot]))))
                for (FName Parameter :
                     {FName("diffuse_color_1"), FName("diffuse_color_2"), FName("B_diffuse_color_1")})
                    Fabric->SetVectorParameterValue(Parameter, Color);
        }
    }
    TInlineComponentArray<UGroomComponent *> Grooms(&Actor);
    for (auto *Groom : Grooms)
    {
        Groom->bUseCards = true;
        Groom->SimulationSettings.bOverrideSettings = true;
        Groom->SimulationSettings.SolverSettings.bEnableSimulation = false;
        if (Groom->GetFName() == TEXT("Peachfuzz"))
            Groom->SetVisibility(false);
        // Apply both settings when recreating render and Niagara resources on this local preview.
        Groom->ReregisterComponent();
    }
}

// Performance Capture Core resets follower relationships when components re-register
// (for example, a Concert package reload). Restore only bindings that have actually changed.
void RefreshFollowers(AActor *Actor)
{
    if (!Actor)
        return;
    auto *Body = FindObjectFast<USkeletalMeshComponent>(Actor, TEXT("Body"));
    auto *Face = FindObjectFast<USkeletalMeshComponent>(Actor, TEXT("Face"));
    auto *Clothes = FindObjectFast<USkeletalMeshComponent>(Actor, TEXT("PerformanceOutfit"));
    if (!Body || !Face)
        return;
    if (Face->GetAnimClass() != UAnimPreviewAttacheInstance::StaticClass())
    {
        Face->SetAnimInstanceClass(UAnimPreviewAttacheInstance::StaticClass());
        Face->SetUpdateAnimationInEditor(true);
        Face->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
        Face->AddTickPrerequisiteComponent(Body);
    }
    if (Clothes && Clothes->LeaderPoseComponent.Get() != Body)
        Clothes->SetLeaderPoseComponent(Body);
}

bool BindMetaHuman(AActor &Actor, USkeletalMeshComponent *Body, FPerformer &Performer)
{
    auto *Identity =
        LoadObject<USkeletalMesh>(nullptr, TEXT("/MetaHumanCharacter/Body/IdentityTemplate/SKM_Body.SKM_Body"));
    auto *Asset = LoadObject<UIKRetargeter>(
        nullptr, TEXT("/MetaHumanCharacter/Animation/Retargeting/RTG_MH_IKRig.RTG_MH_IKRig"));
    if (!Identity || !Asset)
        return false;
    auto *Source = NewObject<USkeletalMeshComponent>(&Actor, TEXT("CaptureIdentity"), RF_Transient);
    Actor.AddInstanceComponent(Source);
    Source->SetupAttachment(Body->GetAttachParent());
    Source->SetRelativeTransform(Body->GetRelativeTransform());
    Source->SetSkeletalMesh(Identity);
    Source->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Source->SetVisibility(false);
    Source->SetHiddenInGame(true);
    Source->SetForcedLOD(1); // Preserve all finger chains on the non-rendering source.
    Source->RegisterComponent();
    if (!UHumanTouchPerformerLibrary::BindPerformer(Source, Performer.Subject))
        return false;

    // Keep the Creator preview's own retarget component independent of this live solve.
    auto *IK = NewObject<URetargetComponent>(&Actor, TEXT("CaptureBodyIK"), RF_Transient);
    Actor.AddInstanceComponent(IK);
    IK->SourceSkeletalMeshComponent.PathToComponent = Source->GetName();
    IK->ControlledSkeletalMeshComponent.PathToComponent = Body->GetName();
    IK->bForceOtherMeshesToFollowControlledMesh = false;
    // Preserve centimetre root travel when the canonical and target pelvis heights differ.
    if (!Performer.LiveRig.IsValid())
    {
        auto *LiveRig = DuplicateObject<UIKRetargeter>(Asset, GetTransientPackage(),
                                                       MakeUniqueObjectName(GetTransientPackage(),
                                                                            UIKRetargeter::StaticClass(),
                                                                            TEXT("RTG_MH_IKRig_Station")));
        LiveRig->ClearFlags(RF_Public | RF_Standalone | RF_Transactional);
        LiveRig->SetFlags(RF_Transient);
        Performer.LiveRig.Reset(LiveRig);
        for (auto *Pelvis : LiveRig->GetAllRetargetOpsOfType<FIKRetargetPelvisMotionOp>())
            Pelvis->Settings.BlendToAbsoluteOffset = FVector::OneVector;
        if (FName(Performer.Subject) == TEXT("Human_Cam_B"))
            UIKRetargeterController::GetController(LiveRig)->AddRetargetOp(
                FHumanTouchFootPlacement::StaticStruct());
    }
    IK->RetargetAsset = Performer.LiveRig.Get();
    IK->RegisterComponent();
    return true;
}
} // namespace

EHumanTouchCast GetHumanTouchCast()
{
    return SelectedCast;
}
void SetHumanTouchCast(EHumanTouchCast Mode)
{
    SelectedCast = Mode;
}

class FHumanTouchPerformersEditor : public IModuleInterface
{
    FPerformer Performers[3] = {
        {TEXT("Victor"),
         TEXT("Human_Quest_A"),
         FVector(70, -110, 0),
         25.f,
         FLinearColor(.045f, .24f, .52f),
         {}},
        {TEXT("Bruce"), TEXT("Human_Cam_B"), FVector(70, 110, 0), 155.f, FLinearColor(.65f, .18f, .035f), {}},
        {TEXT("Ada"), TEXT("Human_Quest_C"), FVector(-100, 0, 0), -90.f, FLinearColor(.035f, .37f, .24f), {}}};
    TMap<TWeakObjectPtr<UWorld>, TArray<TWeakObjectPtr<AActor>>> Spawned;
    EHumanTouchCast ActiveCast = EHumanTouchCast::Mannequins;
    FString MountedPath;
    IConsoleObject *ModeCommand = nullptr;
    FTSTicker::FDelegateHandle Ticker;
    TUniquePtr<FPreviewScene> DriverScene;

    void ClearCast()
    {
        for (const auto &Pair : Spawned)
            for (const auto &Weak : Pair.Value)
                if (AActor *Actor = Weak.Get())
                    Actor->Destroy();
        Spawned.Empty();
        auto *Creator = GEditor ? GEditor->GetEditorSubsystem<UMetaHumanCharacterEditorSubsystem>() : nullptr;
        for (auto &Performer : Performers)
        {
            if (Creator && Performer.Character.IsValid() &&
                Creator->IsObjectAddedForEditing(Performer.Character.Get()))
                Creator->RemoveObjectToEdit(Performer.Character.Get());
            Performer.Garment.Reset();
            Performer.LiveRig.Reset();
            Performer.Character.Reset();
            Performer.Attempted = false;
        }
        DriverScene.Reset();
        GEngine->ForceGarbageCollection(false);
    }

    ASkeletalMeshActor *SpawnMannequin(UWorld *World, const FPerformer &Performer, int32 Index)
    {
        const FString Model = Index == 2 ? TEXT("Quinn") : TEXT("Manny");
        const FString Path = FString::Printf(
            TEXT("/Game/Characters/Mannequins/Meshes/SKM_%s_Simple.SKM_%s_Simple"), *Model, *Model);
        auto *Mesh = LoadObject<USkeletalMesh>(nullptr, *Path);
        if (!Mesh)
        {
            UE_LOG(LogHumanTouchPerformers, Error, TEXT("Missing %s. Install UE Templates and Feature Packs."),
                   *Path);
            return nullptr;
        }
        FActorSpawnParameters Params;
        Params.ObjectFlags = RF_Transient;
        auto *Actor = World->SpawnActor<ASkeletalMeshActor>(
            ASkeletalMeshActor::StaticClass(), Performer.Location, FRotator(0, Performer.Yaw, 0), Params);
        if (!Actor)
            return nullptr;
        Actor->SetActorLabel(Model + TEXT(" - ") + Performer.Subject, false);
        auto *Body = Actor->GetSkeletalMeshComponent();
        Body->SetMobility(EComponentMobility::Movable);
        Body->SetSkeletalMesh(Mesh);
        Body->SetCollisionEnabled(ECollisionEnabled::NoCollision);
        Body->SetForcedLOD(FMath::Min(2, Body->GetNumLODs()));
        UHumanTouchPerformerLibrary::BindPerformer(Body, Performer.Subject);
        return Actor;
    }

    bool Tick(float)
    {
        if (!GEditor || !GEditor->IsInitialized() || IsEngineExitRequested())
            return true;
        if (ActiveCast != SelectedCast)
        {
            ClearCast();
            ActiveCast = SelectedCast;
        }
        for (auto It = Spawned.CreateIterator(); It; ++It)
            if (!It.Key().IsValid())
                It.RemoveCurrent();
        for (const FWorldContext &Context : GEditor->GetWorldContexts())
        {
            UWorld *World = Context.World();
            if ((Context.WorldType != EWorldType::Editor && Context.WorldType != EWorldType::PIE) ||
                !IsStation(World))
                continue;
            auto &Slots = Spawned.FindOrAdd(World);
            const int32 Index = Slots.Num();
            if (Index >= UE_ARRAY_COUNT(Performers))
            {
                if (ActiveCast == EHumanTouchCast::MetaHumans)
                    for (const auto &Entry : Slots)
                        RefreshFollowers(Entry.Get());
                continue;
            }
            FPerformer &Performer = Performers[Index];
            if (ActiveCast == EHumanTouchCast::Mannequins)
            {
                Slots.Add(SpawnMannequin(World, Performer, Index));
                return true;
            }
            auto *Creator = GEditor->GetEditorSubsystem<UMetaHumanCharacterEditorSubsystem>();
            if (!Creator)
                continue;
            // Reserve the slot before the synchronous Creator assembly, which can pump editor callbacks.
            Slots.Add(nullptr);
            UMetaHumanCharacter *Character = PrepareCharacter(Performer, Creator);
            if (!Character)
                continue;
            // Creator's component re-registration callback needs one invisible driver per character.
            // Keep its world alive across Concert map reloads and PIE teardown, as the Creator toolkit does.
            if (!DriverScene)
                DriverScene =
                    MakeUnique<FPreviewScene>(FPreviewScene::ConstructionValues().SetTransactional(false));
            auto Preview = Creator->CreateMetaHumanCharacterEditorActor(Character, World);
            if (!Creator->GetInvisibleDrivingActor(Character))
                Creator->CreateMetaHumanInvisibleDrivingActor(Character, Preview, DriverScene->GetWorld());
            Preview->SetActorDrivingAnimationMode(EMetaHumanActorDrivingAnimationMode::Manual);
            // Lower-detail face and hair with a separate full-joint animation source.
            Preview->SetForcedLOD(5);
            auto *Actor = CastChecked<AActor>(Preview.GetObject());
            Actor->SetActorLabel(FString::Printf(TEXT("%s - %s"), Performer.Preset, Performer.Subject), false);
            Actor->SetActorLocationAndRotation(Performer.Location, FRotator(0, Performer.Yaw, 0));
            const USkeletalMeshComponent *PreviewBody = Preview->GetBodyComponent();
            auto *Body = const_cast<USkeletalMeshComponent *>(PreviewBody);
            const bool Bound = BindMetaHuman(*Actor, Body, Performer);
            DressForXR(*Actor, Body, Performer);
            RefreshFollowers(Actor);
            Spawned.FindChecked(World)[Index] = Actor;
            UE_LOG(LogHumanTouchPerformers, Display, TEXT("%s in %s bound to %s: %s"), Performer.Preset,
                   *World->GetPathName(), Performer.Subject, Bound ? TEXT("yes") : TEXT("failed"));
            return true;
        }
        return true;
    }

    TSharedRef<SDockTab> OpenWidget(const FSpawnTabArgs &)
    {
        // This control has no world data. An open utility tab must not retain an old Concert map.
        auto *Widget = NewObject<UHumanTouchPerformerWidget>(GetTransientPackage());
        Widget->Initialize();
        return SNew(SDockTab).TabRole(ETabRole::NomadTab)[Widget->TakeWidget()];
    }

    static void SetMode(const FString &Value)
    {
        if (Value.Equals(TEXT("MetaHumans"), ESearchCase::IgnoreCase))
            SetHumanTouchCast(EHumanTouchCast::MetaHumans);
        else if (Value.Equals(TEXT("Mannequins"), ESearchCase::IgnoreCase))
            SetHumanTouchCast(EHumanTouchCast::Mannequins);
        else
            UE_LOG(LogHumanTouchPerformers, Warning, TEXT("Choose MetaHumans or Mannequins, received %s"),
                   *Value);
    }

  public:
    void StartupModule() override
    {
        if (IsRunningCommandlet())
            return;
        FString Mode;
        if (FParse::Value(FCommandLine::Get(), TEXT("HumanTouchPerformers="), Mode))
            SetMode(Mode);
        ActiveCast = SelectedCast;
        const FString ProjectAssets =
            FPaths::ProjectContentDir() / TEXT("Characters/Mannequins/Meshes/SKM_Quinn_Simple.uasset");
        if (!IFileManager::Get().FileExists(*ProjectAssets) && !FPackageName::MountPointExists(TemplateMount))
        {
            auto Path = FPaths::ConvertRelativePathToFull(
                FPaths::EngineDir() /
                TEXT("../Templates/TemplateResources/High/Characters/Content/Mannequins/"));
            FPaths::CollapseRelativeDirectories(Path);
            if (IFileManager::Get().DirectoryExists(*Path))
            {
                MountedPath = Path;
                FPackageName::RegisterMountPoint(TemplateMount, MountedPath);
            }
        }
        FGlobalTabmanager::Get()
            ->RegisterNomadTabSpawner(TabId,
                                      FOnSpawnTab::CreateRaw(this, &FHumanTouchPerformersEditor::OpenWidget))
            .SetDisplayName(NSLOCTEXT("HumanTouch", "Tab", "Capture Performers"))
            .SetMenuType(ETabSpawnerMenuType::Hidden);
        UToolMenus::RegisterStartupCallback(
            FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FHumanTouchPerformersEditor::RegisterMenu));
        ModeCommand = IConsoleManager::Get().RegisterConsoleCommand(
            TEXT("HumanTouch.Performers"),
            TEXT("Open the Capture Performers widget, or specify Mannequins / MetaHumans."),
            FConsoleCommandWithArgsDelegate::CreateLambda(
                [](const TArray<FString> &Args)
                {
                    if (Args.Num() == 0)
                        FGlobalTabmanager::Get()->TryInvokeTab(TabId);
                    else
                        SetMode(Args[0]);
                }));
        Ticker = FTSTicker::GetCoreTicker().AddTicker(
            FTickerDelegate::CreateRaw(this, &FHumanTouchPerformersEditor::Tick), .35f);
    }
    void RegisterMenu()
    {
        FToolMenuOwnerScoped Owner(this);
        UToolMenus::Get()
            ->ExtendMenu("LevelEditor.MainMenu.Tools")
            ->FindOrAddSection("HumanTouch")
            .AddMenuEntry(
                TabId, NSLOCTEXT("HumanTouch", "Menu", "Capture Performers"),
                NSLOCTEXT("HumanTouch", "Hint", "Choose the characters driven by Live Link Hub."), FSlateIcon(),
                FUIAction(FExecuteAction::CreateLambda([] { FGlobalTabmanager::Get()->TryInvokeTab(TabId); })));
    }
    void ShutdownModule() override
    {
        if (IsRunningCommandlet())
            return;
        FTSTicker::GetCoreTicker().RemoveTicker(Ticker);
        if (ModeCommand)
            IConsoleManager::Get().UnregisterConsoleObject(ModeCommand);
        UToolMenus::UnRegisterStartupCallback(this);
        UToolMenus::UnregisterOwner(this);
        if (FSlateApplication::IsInitialized())
            FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(TabId);
        if (GEditor && !IsEngineExitRequested())
            ClearCast();
        // During process exit, mount listeners can already be torn down. The process
        // owns this mount until exit. Remove it explicitly on a live module unload.
        if (!IsEngineExitRequested() && !MountedPath.IsEmpty())
            FPackageName::UnRegisterMountPoint(TemplateMount, MountedPath);
    }
};
IMPLEMENT_MODULE(FHumanTouchPerformersEditor, HumanTouchPerformersEditor)
