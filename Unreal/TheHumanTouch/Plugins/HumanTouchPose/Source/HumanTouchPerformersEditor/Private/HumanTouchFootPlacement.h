#pragma once

#include "Retargeter/IKRetargetOps.h"
#include "HumanTouchFootPlacement.generated.h"

USTRUCT()
struct FHumanTouchFootPlacementSettings : public FIKRetargetOpSettingsBase
{
    GENERATED_BODY()
    virtual void CopySettingsAtRuntime(const FIKRetargetOpSettingsBase *) override
    {
    }
};

/** Retain the webcam ankle trajectory after the stock identity-to-character full-body solve. */
USTRUCT(meta = (DisplayName = "Captured foot placement"))
struct FHumanTouchFootPlacement : public FIKRetargetOpBase
{
    GENERATED_BODY()
    virtual bool Initialize(const FIKRetargetProcessor &, const FRetargetSkeleton &Source,
                            const FTargetSkeleton &Target, const FIKRetargetOpBase *, FIKRigLogger &) override;
    virtual void Run(FIKRetargetProcessor &, double, const TArray<FTransform> &SourcePose,
                     TArray<FTransform> &TargetPose) override;
    virtual FIKRetargetOpSettingsBase *GetSettings() override
    {
        return &Settings;
    }
    virtual const UScriptStruct *GetSettingsType() const override
    {
        return FHumanTouchFootPlacementSettings::StaticStruct();
    }
    virtual const UScriptStruct *GetType() const override
    {
        return StaticStruct();
    }
    UPROPERTY() FHumanTouchFootPlacementSettings Settings;

  private:
    struct FLeg
    {
        int32 SourceFoot, SourceToe, Thigh, Calf, Foot, Toe;
        FVector RestOffset;
    };
    FLeg Legs[2]{};
};
