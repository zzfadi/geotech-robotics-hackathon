#include "AnimPreviewAttacheInstance.h"
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
struct FPerformer
{
    const TCHAR *Preset;
    const TCHAR *Subject;
    FVector Location;
    float Yaw;
    TStrongObjectPtr<UMetaHumanCharacter> Character;
    bool Attempted = false;
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
} // namespace

class FHumanTouchPerformersEditor : public IModuleInterface
{
    FPerformer Performers[3] = {{TEXT("Victor"), TEXT("Human_Quest_A"), FVector(70, -110, 0), 25.f, {}},
                                {TEXT("Bruce"), TEXT("Human_Cam_B"), FVector(70, 110, 0), 155.f, {}},
                                {TEXT("Ada"), TEXT("Human_Quest_C"), FVector(-100, 0, 0), -90.f, {}}};
    // A transient preview is instantiated once per slot, independently in each editor/PIE world.
    TMap<TWeakObjectPtr<UWorld>, int32> NextSlot;
    FTSTicker::FDelegateHandle Ticker;
    TUniquePtr<FPreviewScene> DriverScene;

    bool Tick(float)
    {
        if (!GEditor || !GEditor->IsInitialized() || IsEngineExitRequested())
            return true;
        for (auto It = NextSlot.CreateIterator(); It; ++It)
            if (!It.Key().IsValid())
                It.RemoveCurrent();
        for (const FWorldContext &Context : GEditor->GetWorldContexts())
        {
            UWorld *World = Context.World();
            if ((Context.WorldType != EWorldType::Editor && Context.WorldType != EWorldType::PIE) ||
                !IsStation(World))
                continue;
            int32 &Next = NextSlot.FindOrAdd(World);
            if (Next >= UE_ARRAY_COUNT(Performers))
                continue;
            auto *Creator = GEditor->GetEditorSubsystem<UMetaHumanCharacterEditorSubsystem>();
            if (!Creator)
                continue;
            FPerformer &Performer = Performers[Next++];
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
            // LOD 3 retains the articulated body and fingers while reducing face and groom cost in VR.
            Preview->SetForcedLOD(3);
            auto *Actor = CastChecked<AActor>(Preview.GetObject());
            Actor->SetActorLabel(FString::Printf(TEXT("%s - %s"), Performer.Preset, Performer.Subject), false);
            Actor->SetActorLocationAndRotation(Performer.Location, FRotator(0, Performer.Yaw, 0));
            const USkeletalMeshComponent *PreviewBody = Preview->GetBodyComponent();
            auto *Body = const_cast<USkeletalMeshComponent *>(PreviewBody);
            // Match the body's animated neck and spine while keeping the facial bones in their neutral pose.
            const USkeletalMeshComponent *PreviewFace = Preview->GetFaceComponent();
            auto *Face = const_cast<USkeletalMeshComponent *>(PreviewFace);
            Face->SetAnimInstanceClass(UAnimPreviewAttacheInstance::StaticClass());
            Face->SetUpdateAnimationInEditor(true);
            Face->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
            Face->AddTickPrerequisiteComponent(Body);
            const bool Bound = UHumanTouchPerformerLibrary::BindPerformer(Body, Performer.Subject);
            UE_LOG(LogHumanTouchPerformers, Display, TEXT("%s in %s bound to %s: %s"), Performer.Preset,
                   *World->GetPathName(), Performer.Subject, Bound ? TEXT("yes") : TEXT("failed"));
            return true;
        }
        return true;
    }

  public:
    void StartupModule() override
    {
        if (!IsRunningCommandlet())
            Ticker = FTSTicker::GetCoreTicker().AddTicker(
                FTickerDelegate::CreateRaw(this, &FHumanTouchPerformersEditor::Tick), 2.f);
    }

    void ShutdownModule() override
    {
        FTSTicker::GetCoreTicker().RemoveTicker(Ticker);
        if (GEditor && !IsEngineExitRequested())
        {
            auto *Creator = GEditor->GetEditorSubsystem<UMetaHumanCharacterEditorSubsystem>();
            for (auto &Performer : Performers)
                if (Creator && Performer.Character.IsValid() &&
                    Creator->IsObjectAddedForEditing(Performer.Character.Get()))
                    Creator->RemoveObjectToEdit(Performer.Character.Get());
        }
    }
};

IMPLEMENT_MODULE(FHumanTouchPerformersEditor, HumanTouchPerformersEditor)
