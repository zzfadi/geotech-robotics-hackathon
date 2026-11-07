#include "Containers/Ticker.h"
#include "DrawDebugHelpers.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Features/IModularFeatures.h"
#include "HAL/IConsoleManager.h"
#include "ILiveLinkClient.h"
#include "Roles/LiveLinkAnimationRole.h"
#include "Roles/LiveLinkAnimationTypes.h"

namespace
{
TAutoConsoleVariable<int32> Preview(TEXT("HumanTouch.Pose.Preview"), 0,
    TEXT("Draw live skeletons in the current editor or game world. Set to 1 to enable."));

void DrawSubject(UWorld* World, ILiveLinkClient& Client, FName Name, FColor Color)
{
    FLiveLinkSubjectFrameData Sample;
    if (!Client.IsSubjectValid(Name) ||
        !Client.EvaluateFrame_AnyThread(Name, ULiveLinkAnimationRole::StaticClass(), Sample)) return;
    const auto* Frame = Sample.FrameData.Cast<FLiveLinkAnimationFrameData>();
    const auto* Static = Sample.StaticData.Cast<FLiveLinkSkeletonStaticData>();
    if (!Frame || !Static || Static->GetBoneParents().Num() != Frame->Transforms.Num()) return;
    // A hip-relative solve has no measured room translation. Lift it to a standing
    // preview location. Registered data always uses its actual station coordinates.
    const FVector Offset = Frame->MetaData.StringMetaData.FindRef(TEXT("space")) == TEXT("hip_relative")
        ? FVector(0, 0, 100) : FVector::ZeroVector;
    TArray<FTransform> Accumulated;
    Accumulated.Reserve(Frame->Transforms.Num());
    for (int32 I = 0; I < Frame->Transforms.Num(); ++I)
    {
        const int32 Parent = Static->GetBoneParents()[I];
        if (Parent >= I || Parent < INDEX_NONE) return;
        Accumulated.Add(Parent == INDEX_NONE ? Frame->Transforms[I] : Frame->Transforms[I] * Accumulated[Parent]);
        if (Frame->PropertyValues.IsValidIndex(I) && Frame->PropertyValues[I] < .35f) continue;
        const FVector End = Offset + Accumulated[I].GetTranslation();
        DrawDebugPoint(World, End, 4, Color, false, 0, 0);
        if (Parent >= 0 && (!Frame->PropertyValues.IsValidIndex(Parent) || Frame->PropertyValues[Parent] >= .35f))
            DrawDebugLine(World, Offset + Accumulated[Parent].GetTranslation(), End, Color, false, 0, 0, 1.0f);
    }
}
}

// One transient draw per tick avoids editing the level or emitting Concert transactions.
class FHumanTouchPosePreview : public FTSTickerObjectBase
{
public:
    bool Tick(float) override
    {
        if (!Preview.GetValueOnGameThread() || !GEngine) return true;
        auto& Features = IModularFeatures::Get();
        if (!Features.IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName)) return true;
        auto& Client = Features.GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName);
        for (const auto& Context : GEngine->GetWorldContexts())
        {
            if (Context.World() && (Context.WorldType == EWorldType::Editor ||
                Context.WorldType == EWorldType::PIE || Context.WorldType == EWorldType::Game))
            {
                DrawSubject(Context.World(), Client, TEXT("Human_Cam_B"), FColor::Cyan);
                DrawSubject(Context.World(), Client, TEXT("Human_Quest_A"), FColor::Orange);
                DrawSubject(Context.World(), Client, TEXT("Human_Quest_C"), FColor::Purple);
            }
        }
        return true;
    }
};

#include "Modules/ModuleManager.h"
class FHumanTouchPoseModule : public IModuleInterface
{
    TUniquePtr<FHumanTouchPosePreview> Ticker;
    void StartupModule() override { Ticker = MakeUnique<FHumanTouchPosePreview>(); }
    void ShutdownModule() override { Ticker.Reset(); }
};
IMPLEMENT_MODULE(FHumanTouchPoseModule, HumanTouchPose)
