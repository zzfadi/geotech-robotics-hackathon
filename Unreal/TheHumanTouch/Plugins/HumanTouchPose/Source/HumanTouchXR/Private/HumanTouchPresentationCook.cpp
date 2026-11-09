#include "HumanTouchPresentationCook.h"
#include "Camera/CameraActor.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/LightComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SkyLightComponent.h"
#include "Engine/Level.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/World.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/ObjectSaveContext.h"
#include "UObject/UObjectGlobals.h"

namespace
{
FDelegateHandle SaveHandle;
TSet<FString> Props, Fills;
double SunLux, SkyIntensity, Exposure, Bloom;

void Apply(AActor *Actor)
{
    if (!Actor)
        return;
    const FString &Name = Actor->GetActorLabel(false);
    FString Prefix, Leaf;
    if (!Name.Split(TEXT("__"), &Prefix, &Leaf, ESearchCase::CaseSensitive, ESearchDir::FromEnd))
        Leaf = Name;
    const bool bHousing = Props.Contains(Leaf);
    if (bHousing || (Actor->IsA<ACameraActor>() && Leaf.StartsWith(TEXT("MakerCamera_"))))
    {
        TInlineComponentArray<UPrimitiveComponent *> Primitives(Actor);
        for (auto *Primitive : Primitives)
        {
            const FName Class = Primitive->GetClass()->GetFName();
            if (bHousing || Class == TEXT("CameraProxyMeshComponent") || Class == TEXT("DrawFrustumComponent") ||
                Class == TEXT("BillboardComponent"))
            {
                Primitive->SetHiddenInGame(true);
                Primitive->SetVisibility(false);
            }
        }
    }
    if (Fills.Contains(Leaf))
        if (auto *Light = Actor->FindComponentByClass<ULightComponent>())
            Light->SetIntensity(0);
    if (Name == TEXT("MakerLighting_GlobalDaylight"))
        if (auto *Sun = Actor->FindComponentByClass<UDirectionalLightComponent>())
        {
            Sun->SetIntensity(SunLux);
            Sun->SetCastShadows(true);
        }
    if (Name == TEXT("MakerLighting_Ambient"))
        if (auto *Sky = Actor->FindComponentByClass<USkyLightComponent>())
        {
            Sky->SourceType = SLS_CapturedScene;
            Sky->SetCubemap(nullptr);
            Sky->SetIntensity(SkyIntensity);
        }
    if (Name == TEXT("MakerLighting_Exposure"))
        if (auto *Volume = Cast<APostProcessVolume>(Actor))
        {
            Volume->Settings.bOverride_AutoExposureBias = true;
            Volume->Settings.AutoExposureBias = Exposure;
            Volume->Settings.bOverride_BloomIntensity = true;
            Volume->Settings.BloomIntensity = Bloom;
        }
}
} // namespace
void HumanTouchPresentationCook::Startup()
{
    if (!IsRunningCookCommandlet())
        return;
    FString Text;
    TSharedPtr<FJsonObject> Policy;
    if (!FFileHelper::LoadFileToString(Text, *(FPaths::ProjectConfigDir() / TEXT("StationPresentation.json"))) ||
        !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Policy) || !Policy)
    {
        UE_LOG(LogTemp, Error, TEXT("Missing or unreadable Human Touch station presentation policy"));
        return;
    }
    for (const auto &V : Policy->GetArrayField(TEXT("camera_props")))
        Props.Add(V->AsString());
    for (const auto &V : Policy->GetArrayField(TEXT("studio_fills")))
        Fills.Add(V->AsString());
    SunLux = Policy->GetNumberField(TEXT("daylight_lux"));
    SkyIntensity = Policy->GetNumberField(TEXT("sky_intensity"));
    Exposure = Policy->GetNumberField(TEXT("exposure_bias"));
    Bloom = Policy->GetNumberField(TEXT("bloom_intensity"));
    // Bake game-visible properties into the cooked map. The packaged game does not require editor Python or labels.
    SaveHandle = FCoreUObjectDelegates::OnObjectPreSave.AddLambda([](UObject *Object, FObjectPreSaveContext Context) {
        auto *World = Cast<UWorld>(Object);
        if (Context.IsCooking() && World && World->GetOutermost()->GetName() == TEXT("/Game/Maps/TheHumanTouch") &&
            World->PersistentLevel)
            for (AActor *Actor : World->PersistentLevel->Actors)
                Apply(Actor);
    });
}
void HumanTouchPresentationCook::Shutdown()
{
    FCoreUObjectDelegates::OnObjectPreSave.Remove(SaveHandle);
}
