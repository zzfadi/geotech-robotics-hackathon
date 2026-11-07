#include "HumanTouchFootPlacement.h"
#include "Retargeter/IKRetargetProcessor.h"
#include "TwoBoneIK.h"

bool FHumanTouchFootPlacement::Initialize(const FIKRetargetProcessor &, const FRetargetSkeleton &Source,
                                          const FTargetSkeleton &Target, const FIKRetargetOpBase *,
                                          FIKRigLogger &)
{
    bIsInitialized = false;
    for (int32 Side = 0; Side < 2; ++Side)
    {
        const FString Suffix = Side == 0 ? TEXT("_l") : TEXT("_r");
        const FName Ankle(*(TEXT("foot") + Suffix));
        const FName Toe(*(TEXT("ball") + Suffix));
        FLeg &Leg = Legs[Side];
        Leg.SourceFoot = Source.FindBoneIndexByName(Ankle);
        Leg.SourceToe = Source.FindBoneIndexByName(Toe);
        Leg.Thigh = Target.FindBoneIndexByName(FName(*(TEXT("thigh") + Suffix)));
        Leg.Calf = Target.FindBoneIndexByName(FName(*(TEXT("calf") + Suffix)));
        Leg.Foot = Target.FindBoneIndexByName(Ankle);
        Leg.Toe = Target.FindBoneIndexByName(Toe);
        if (Leg.SourceFoot < 0 || Leg.SourceToe < 0 || Leg.Thigh < 0 || Leg.Calf < 0 || Leg.Foot < 0 || Leg.Toe < 0)
            return false;
        Leg.RestOffset = Target.RetargetPoses.GetGlobalRetargetPose()[Leg.Foot].GetTranslation() -
                         Source.RetargetPoses.GetGlobalRetargetPose()[Leg.SourceFoot].GetTranslation();
    }
    bIsInitialized = true;
    return true;
}

void FHumanTouchFootPlacement::Run(FIKRetargetProcessor &Processor, double,
                                   const TArray<FTransform> &SourcePose, TArray<FTransform> &TargetPose)
{
    const auto &Target = Processor.GetSkeleton(ERetargetSourceOrTarget::Target);
    for (const FLeg &Leg : Legs)
    {
        FTransform Thigh = TargetPose[Leg.Thigh], Calf = TargetPose[Leg.Calf], Foot = TargetPose[Leg.Foot];
        const FVector Goal = SourcePose[Leg.SourceFoot].GetTranslation() + Leg.RestOffset;
        // Preserve the stock knee plane and each target segment length.
        const FVector ToeAxis = Foot.InverseTransformPosition(TargetPose[Leg.Toe].GetTranslation()).GetSafeNormal();
        AnimationCore::SolveTwoBoneIK(Thigh, Calf, Foot, Calf.GetTranslation(), Goal, false, 1., 1.);
        // A stationary ankle can still pivot the forefoot as the knee bends. Match the
        // captured ankle-to-toe direction while retaining the stock roll about that axis.
        const FVector Observed = (SourcePose[Leg.SourceToe].GetTranslation()
                                - SourcePose[Leg.SourceFoot].GetTranslation()).GetSafeNormal();
        if (!ToeAxis.IsNearlyZero() && !Observed.IsNearlyZero())
            Foot.SetRotation((FQuat::FindBetweenNormals(Foot.GetRotation().RotateVector(ToeAxis), Observed)
                             * Foot.GetRotation()).GetNormalized());
        Target.SetGlobalTransformAndUpdateChildren(Leg.Thigh, Thigh, TargetPose);
        Target.SetGlobalTransformAndUpdateChildren(Leg.Calf, Calf, TargetPose);
        Target.SetGlobalTransformAndUpdateChildren(Leg.Foot, Foot, TargetPose);
    }
}
