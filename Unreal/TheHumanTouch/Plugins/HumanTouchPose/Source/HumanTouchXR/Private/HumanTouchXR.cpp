#include "HumanTouchPassthrough.h"
#include "HumanTouchXRTable.h"

#include "Components/PrimitiveComponent.h"
#include "Containers/Ticker.h"
#include "Engine/Engine.h"
#include "EngineUtils.h"
#include "GlobalShader.h"
#include "HAL/IConsoleManager.h"
#include "Interfaces/IPluginManager.h"
#include "IXRTrackingSystem.h"
#include "Modules/ModuleManager.h"
#include "OpenXRBlueprintFunctionLibrary.h"
#include "PostProcess/PostProcessMaterialInputs.h"
#include "SceneInterface.h"
#include "SceneViewExtension.h"
#include "ScreenPass.h"
#include "ShaderCore.h"
#include "StereoRendering.h"
#include "UnrealClient.h"

DEFINE_LOG_CATEGORY_STATIC(LogHumanTouchXR, Log, All);

namespace
{
bool bRequested = false;
bool bActive = false;
bool bKeyVideo = false;
using EBlend = EOpenXREnvironmentBlendMode;
using FOpenXR = UOpenXRBlueprintFunctionLibrary;

bool IsHeadsetView(const FSceneView& View)
{
    return !View.bIsSceneCapture && View.GetFeatureLevel() >= ERHIFeatureLevel::SM5 && IStereoRendering::IsStereoEyeView(View);
}

class FRoomMaskPS : public FGlobalShader
{
    DECLARE_GLOBAL_SHADER(FRoomMaskPS);
    SHADER_USE_PARAMETER_STRUCT(FRoomMaskPS, FGlobalShader);
    BEGIN_SHADER_PARAMETER_STRUCT(FParameters, )
        SHADER_PARAMETER_STRUCT_REF(FViewUniformShaderParameters, View)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, SceneColorInput)
        SHADER_PARAMETER_SAMPLER(SamplerState, ColorSampler)
        SHADER_PARAMETER_RDG_TEXTURE(Texture2D, SceneDepthInput)
        SHADER_PARAMETER(FVector4f, OutputRect)
        SHADER_PARAMETER(float, GuardBlackKey)
        RENDER_TARGET_BINDING_SLOTS()
    END_SHADER_PARAMETER_STRUCT()
    static bool ShouldCompilePermutation(const FGlobalShaderPermutationParameters& P)
    {
        return IsFeatureLevelSupported(P.Platform, ERHIFeatureLevel::SM5);
    }
};
IMPLEMENT_GLOBAL_SHADER(FRoomMaskPS, "/HumanTouchXR/Private/QuestPassthrough.usf", "MainPS", SF_Pixel);

class FRoomView : public FSceneViewExtensionBase
{
public:
    FRoomView(const FAutoRegister& R, bool bInKeyVideo) : FSceneViewExtensionBase(R), bUseKey(bInKeyVideo) {}

    void SetupView(FSceneViewFamily& Family, FSceneView& View) override
    {
        if (!IsHeadsetView(View) || !Family.Scene)
            return;
        UWorld* World = Family.Scene->GetWorld();
        if (!World)
            return;
        // Do not hide actors in the world: other viewports and Concert clients
        // must retain their scenery and the collision floor must stay solid.
        const double Now = FPlatformTime::Seconds();
        if (World != CachedWorld.Get() || Now >= NextRefresh)
        {
            const bool bNewWorld = World != CachedWorld.Get();
            RoomComponents.Reset();
            for (TActorIterator<AActor> It(World); It; ++It)
            {
                const FString& Label = It->GetActorLabel(false);
                if (!Label.StartsWith(TEXT("MakerEnvironment_")) &&
                    !((Label.StartsWith(TEXT("MakerFleet_")) || Label.StartsWith(TEXT("MakerInteraction_T09__"))) &&
                      (Label.Contains(TEXT("__MakerEnvironment_")) || Label.Contains(TEXT("__WallNumber_")))) &&
                    !It->ActorHasTag(TEXT("PassthroughBackground")))
                    continue;
                TInlineComponentArray<UPrimitiveComponent*> Components(*It);
                for (const UPrimitiveComponent* Component : Components)
                    RoomComponents.Add(Component->GetPrimitiveSceneId());
            }
            if (bNewWorld)
                UE_LOG(LogHumanTouchXR, Display, TEXT("Headset room mask: %d environment primitives hidden in %s."),
                       RoomComponents.Num(), *World->GetName());
            CachedWorld = World;
            NextRefresh = Now + 0.5;
        }
        View.HiddenPrimitives.Append(RoomComponents);
        View.BackgroundColor = FLinearColor::Transparent;
        Family.EngineShowFlags.SetGrid(false);
        Family.EngineShowFlags.SetAtmosphere(false);
        Family.EngineShowFlags.SetFog(false);
        Family.EngineShowFlags.SetCloud(false);
        Family.EngineShowFlags.SetBillboardSprites(false);
        Family.EngineShowFlags.SetSelectionOutline(false);
    }

    void SubscribeToPostProcessingPass(EPostProcessingPass Pass, const FSceneView& View,
                                       FPostProcessingPassDelegateArray& Callbacks, bool) override
    {
        if (Pass == EPostProcessingPass::Tonemap && IsHeadsetView(View))
            Callbacks.Add(FPostProcessingPassDelegate::CreateSP(this, &FRoomView::Mask));
    }

private:
    FScreenPassTexture Mask(FRDGBuilder& Graph, const FSceneView& View, const FPostProcessMaterialInputs& Inputs)
    {
        const FScreenPassTexture Color = FScreenPassTexture::CopyFromSlice(Graph, Inputs.GetInput(EPostProcessMaterialInput::SceneColor));
        if (!Inputs.SceneTextures.SceneTextures)
            return Inputs.ReturnUntouchedSceneColorForPostProcessing(Graph);
        FScreenPassRenderTarget Output = Inputs.OverrideOutput;
        if (!Output.IsValid())
            Output = FScreenPassRenderTarget::CreateFromInput(Graph, Color, View.GetOverwriteLoadAction(), TEXT("HumanTouch.RoomMask"));
        auto* P = Graph.AllocParameters<FRoomMaskPS::FParameters>();
        P->View = View.ViewUniformBuffer;
        P->SceneColorInput = Color.Texture;
        P->ColorSampler = TStaticSamplerState<SF_Point>::GetRHI();
        P->SceneDepthInput = Inputs.SceneTextures.SceneTextures->GetParameters()->SceneDepthTexture;
        const FIntRect Rect = Output.ViewRect;
        P->OutputRect = FVector4f(Rect.Min.X, Rect.Min.Y, 1.f/Rect.Width(), 1.f/Rect.Height());
        P->GuardBlackKey = bUseKey ? 1.f : 0.f;
        P->RenderTargets[0] = Output.GetRenderTargetBinding();
        const auto* Shaders = GetGlobalShaderMap(View.GetFeatureLevel());
        AddDrawScreenPass(Graph, RDG_EVENT_NAME("HumanTouch.RoomMask"), View, FScreenPassTextureViewport(Output),
                          FScreenPassTextureViewport(Color), TShaderMapRef<FScreenPassVS>(Shaders), TShaderMapRef<FRoomMaskPS>(Shaders), P);
        return Output;
    }

    const bool bUseKey;
    TWeakObjectPtr<UWorld> CachedWorld;
    TSet<FPrimitiveComponentId> RoomComponents;
    double NextRefresh = 0;
};
TSharedPtr<FRoomView, ESPMode::ThreadSafe> RoomView;

void Apply()
{
    if (!bRequested && !bActive)
        return;
    if (!GEngine || !GEngine->XRSystem.IsValid() || GEngine->XRSystem->GetSystemName() != TEXT("OpenXR") ||
        !GEngine->StereoRenderingDevice.IsValid() || !GEngine->StereoRenderingDevice->IsStereoEnabled())
    {
        RoomView.Reset();
        bActive = false;
        return;
    }
    const auto Current = FOpenXR::GetEnvironmentBlendMode();
    if (bRequested && HumanTouchPassthrough::IsSupported())
    {
        if (Current != EBlend::AlphaBlend)
            FOpenXR::SetEnvironmentBlendMode(EBlend::AlphaBlend);
        if (FOpenXR::GetEnvironmentBlendMode() == EBlend::AlphaBlend && !RoomView.IsValid())
        {
            RoomView = FSceneViewExtensions::NewExtension<FRoomView>(bKeyVideo);
            bActive = true;
            UE_LOG(LogHumanTouchXR, Display, TEXT("Room passthrough enabled for the headset view."));
        }
    }
    else if (bActive)
    {
        FOpenXR::SetEnvironmentBlendMode(EBlend::Opaque);
        RoomView.Reset();
        bActive = false;
        UE_LOG(LogHumanTouchXR, Display, TEXT("Opaque headset view restored."));
    }
}
}

bool HumanTouchPassthrough::IsRequested() { return bRequested; }
bool HumanTouchPassthrough::IsActive() { return bActive; }
bool HumanTouchPassthrough::IsSupported()
{
    return GEngine && GEngine->XRSystem.IsValid() && GEngine->XRSystem->GetSystemName() == TEXT("OpenXR")
        && FOpenXR::GetSupportedEnvironmentBlendModes().Contains(EBlend::AlphaBlend);
}
void HumanTouchPassthrough::Request(bool bEnable)
{
    bRequested = bEnable;
    Apply();
    if (bRequested && !bActive)
        UE_LOG(LogHumanTouchXR, Warning, TEXT("Passthrough requested; waiting for an OpenXR runtime with alpha-blend support."));
}

class FHumanTouchXRModule : public IModuleInterface
{
    FTSTicker::FDelegateHandle Tick;
    IConsoleObject* Command = nullptr;
public:
    void StartupModule() override
    {
        const FString ShaderDir = IPluginManager::Get().FindPlugin(TEXT("HumanTouchPose"))->GetBaseDir() / TEXT("Shaders");
        AddShaderSourceDirectoryMapping(TEXT("/HumanTouchXR"), ShaderDir);
        if (IsRunningCommandlet())
            return;
        HumanTouchXRTable::Startup();
        bRequested = FParse::Param(FCommandLine::Get(), TEXT("HumanTouchPassthrough"));
        bKeyVideo = FParse::Param(FCommandLine::Get(), TEXT("HumanTouchVideoKey")) ||
                    !FPlatformMisc::GetEnvironmentVariable(TEXT("OXRSYS_TRANSPORT")).IsEmpty();
        Command = IConsoleManager::Get().RegisterConsoleCommand(TEXT("HumanTouch.Passthrough"),
            TEXT("HumanTouch.Passthrough on|off|status"), FConsoleCommandWithArgsDelegate::CreateLambda(
            [](const TArray<FString>& Args)
            {
                if (Args.Num() == 1 && (Args[0].Equals(TEXT("on"), ESearchCase::IgnoreCase) || Args[0] == TEXT("1")))
                    HumanTouchPassthrough::Request(true);
                else if (Args.Num() == 1 && (Args[0].Equals(TEXT("off"), ESearchCase::IgnoreCase) || Args[0] == TEXT("0")))
                    HumanTouchPassthrough::Request(false);
                else
                    UE_LOG(LogHumanTouchXR, Display, TEXT("Passthrough requested=%d active=%d alphaSupported=%d videoKey=%d"),
                           bRequested, bActive, HumanTouchPassthrough::IsSupported(), bKeyVideo);
            }));
        Tick = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([](float){ Apply(); return true; }), 0.5f);
    }
    void ShutdownModule() override
    {
        if (!IsRunningCommandlet()) HumanTouchXRTable::Shutdown();
        FTSTicker::GetCoreTicker().RemoveTicker(Tick);
        if (Command) IConsoleManager::Get().UnregisterConsoleObject(Command);
        RoomView.Reset();
        bActive = false;
    }
};
IMPLEMENT_MODULE(FHumanTouchXRModule, HumanTouchXR)
