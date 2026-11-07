#pragma once

#include "Kismet/BlueprintFunctionLibrary.h"
#include "LiveLinkRetargetAsset.h"
#include "HumanTouchRetarget.generated.h"

UCLASS()
class HUMANTOUCHPOSE_API UHumanTouchRetarget : public ULiveLinkRetargetAsset
{
    GENERATED_BODY()
  public:
    virtual void Initialize() override;
    virtual void BuildPoseFromAnimationData(float DeltaTime, const FLiveLinkSkeletonStaticData *Skeleton,
                                            const FLiveLinkAnimationFrameData *Frame,
                                            FCompactPose &Pose) override;

  private:
    FTransform MeshToWorld;
    TMap<FName, FQuat> HandNeutral;
    struct FLegCalibration
    {
        FVector Anchor;
        double Scale;
    };
    TMap<FName, FLegCalibration> LegCalibration;
    FString LastSession, LastSpace;
    FVector SourceOrigin = FVector::ZeroVector;
    FQuat Alignment = FQuat::Identity, FirstHead = FQuat::Identity;
};

UCLASS()
class HUMANTOUCHPOSE_API UHumanTouchPerformerLibrary : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()
  public:
    UFUNCTION(BlueprintCallable, Category = "Human Touch")
    static bool BindPerformer(USkeletalMeshComponent *Body, FName Subject);
};
