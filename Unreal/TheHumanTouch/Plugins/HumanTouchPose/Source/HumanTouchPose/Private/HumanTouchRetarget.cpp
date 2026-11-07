#include "HumanTouchRetarget.h"
#include "Animation/AnimInstance.h"
#include "BonePose.h"
#include "Components/SkeletalMeshComponent.h"
#include "LiveLinkInstance.h"
#include "Roles/LiveLinkAnimationTypes.h"

void UHumanTouchRetarget::Initialize()
{
    if (const auto *Anim = Cast<UAnimInstance>(GetOuter()))
        if (const auto *Mesh = Anim->GetSkelMeshComponent())
            MeshToWorld = Mesh->GetComponentTransform();
    LastSession.Reset();
    LastSpace.Reset();
}

bool UHumanTouchPerformerLibrary::BindPerformer(USkeletalMeshComponent *Body, FName Subject)
{
    if (!IsValid(Body) || !Body->GetSkeletalMeshAsset() || Subject.IsNone())
        return false;
    Body->SetAnimInstanceClass(ULiveLinkInstance::StaticClass());
    Body->VisibilityBasedAnimTickOption = EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones;
#if WITH_EDITOR
    Body->SetUpdateAnimationInEditor(true);
#endif
    Body->InitAnim(true);
    auto *Anim = Cast<ULiveLinkInstance>(Body->GetAnimInstance());
    if (!Anim)
        return false;
    Anim->SetSubject(FLiveLinkSubjectName(Subject));
    Anim->SetRetargetAsset(UHumanTouchRetarget::StaticClass());
    Anim->EnableLiveLinkEvaluation(true);
    return true;
}

namespace
{
struct FAim
{
    FName Source, End, TargetChild, Previous;
};
struct FRigMap
{
    TMap<FName, FAim> Aim;
    explicit FRigMap(bool Quest)
    {
        auto Add = [this](FString Target, FString Src, FString End, FString Child, FString Previous = FString())
        { Aim.Add(FName(*Target), {FName(*Src), FName(*End), FName(*Child), FName(*Previous)}); };
        for (bool Left : {true, false})
        {
            const FString Side = Left ? TEXT("left") : TEXT("right");
            const FString S = Left ? TEXT("_l") : TEXT("_r");
            const FString Arm = Side + (Quest ? TEXT("-arm-upper") : TEXT("_upper_arm"));
            const FString Elbow = Side + (Quest ? TEXT("-arm-lower") : TEXT("_forearm"));
            const FString Wrist = Side + (Quest ? TEXT("-hand-wrist") : TEXT("_hand"));
            Add("upperarm" + S, Arm, Elbow, "lowerarm" + S);
            Add("lowerarm" + S, Elbow, Wrist, "hand" + S);
            Add("hand" + S, Wrist, Side + (Quest ? TEXT("-hand-middle-phalanx-proximal") : TEXT("_index")),
                (Quest ? TEXT("middle_01") : TEXT("index_01")) + S);
            if (!Quest)
            {
                Add("thigh" + S, Side + "_thigh", Side + "_shin", "calf" + S);
                Add("calf" + S, Side + "_shin", Side + "_foot", "foot" + S);
                Add("foot" + S, Side + "_foot", Side + "_toe", "ball" + S);
                continue;
            }
            for (const FString Finger :
                 {FString("thumb"), FString("index"), FString("middle"), FString("ring"), FString("pinky")})
            {
                const bool Thumb = Finger == TEXT("thumb");
                const FString Prefix =
                    Side + TEXT("-hand-") + (Finger == TEXT("pinky") ? TEXT("little") : *Finger) + TEXT("-");
                const FString F = Finger + TEXT("_");
                if (!Thumb)
                    Add(F + "metacarpal" + S, Prefix + "metacarpal", Prefix + "phalanx-proximal", F + "01" + S);
                Add(F + "01" + S, Prefix + (Thumb ? "metacarpal" : "phalanx-proximal"),
                    Prefix + (Thumb ? "phalanx-proximal" : "phalanx-intermediate"), F + "02" + S);
                Add(F + "02" + S, Prefix + (Thumb ? "phalanx-proximal" : "phalanx-intermediate"),
                    Prefix + "phalanx-distal", F + "03" + S);
                Add(F + "03" + S, Prefix + "phalanx-distal", Prefix + "tip", "", F + "02" + S);
            }
        }
        Add("spine_01", Quest ? "spine-lower" : "pelvis", Quest ? "spine-middle" : "chest", "spine_02");
        Add("spine_03", Quest ? "spine-upper" : "pelvis", "chest", "spine_04");
        Add("neck_01", Quest ? "neck" : "chest", "head", "head");
    }
};
} // namespace

void UHumanTouchRetarget::BuildPoseFromAnimationData(float, const FLiveLinkSkeletonStaticData *Skeleton,
                                                     const FLiveLinkAnimationFrameData *Frame,
                                                     FCompactPose &Pose)
{
    if (!Skeleton || !Frame || Frame->Transforms.IsEmpty())
        return;
    const auto &Names = Skeleton->GetBoneNames();
    const auto &Parents = Skeleton->GetBoneParents();
    if (Names.Num() != Parents.Num() || Names.Num() != Frame->Transforms.Num())
        return;
    TMap<FName, FTransform> Src;
    TArray<FTransform> SourceCS;
    for (int32 I = 0; I < Names.Num(); ++I)
    {
        const int32 P = Parents[I];
        if (P < INDEX_NONE || P >= I)
            return;
        SourceCS.Add(P < 0 ? Frame->Transforms[I] : Frame->Transforms[I] * SourceCS[P]);
        if (!Frame->PropertyValues.IsValidIndex(I) || Frame->PropertyValues[I] >= .35f)
            Src.Add(Names[I], SourceCS[I]);
    }
    const bool Quest = Names[0] == TEXT("hips");
    const FTransform *Pelvis = Src.Find(Quest ? TEXT("hips") : TEXT("pelvis"));
    const FTransform *L = Src.Find(Quest ? TEXT("left-arm-upper") : TEXT("left_upper_arm"));
    const FTransform *R = Src.Find(Quest ? TEXT("right-arm-upper") : TEXT("right_upper_arm"));
    const FTransform *Chest = Src.Find(TEXT("chest"));
    const FTransform *Head = Src.Find(TEXT("head"));
    if (!Pelvis || !L || !R || !Chest)
        return;
    const auto &Bones = Pose.GetBoneContainer();
    const auto &Ref = Bones.GetReferenceSkeleton();
    const auto &RefLocal = Ref.GetRefBonePose();
    TArray<FTransform> Rest;
    Rest.SetNumUninitialized(RefLocal.Num());
    for (int32 I = 0; I < Rest.Num(); ++I)
    {
        const int32 P = Ref.GetParentIndex(I);
        Rest[I] = P < 0 ? RefLocal[I] : RefLocal[I] * Rest[P];
    }
    const int32 TL = Ref.FindBoneIndex("upperarm_l"), TR = Ref.FindBoneIndex("upperarm_r"),
                TP = Ref.FindBoneIndex("pelvis"), TH = Ref.FindBoneIndex("head");
    if (TL < 0 || TR < 0 || TP < 0 || TH < 0)
        return;
    const FVector Right = R->GetTranslation() - L->GetTranslation(),
                  Up = Chest->GetTranslation() - Pelvis->GetTranslation();
    if (Right.IsNearlyZero() || Up.IsNearlyZero())
        return;
    const FVector RefRight = Rest[TR].GetTranslation() - Rest[TL].GetTranslation();
    const FQuat RefBasis = FRotationMatrix::MakeFromYZ(RefRight, FVector::UpVector).ToQuat();
    const FString Session = Frame->MetaData.StringMetaData.FindRef(TEXT("session"));
    const FString Space = Frame->MetaData.StringMetaData.FindRef(TEXT("space"));
    const bool Relative = (Space == TEXT("hip_relative") || Space == TEXT("quest_local_floor"));
    if (Session != LastSession || Space != LastSpace)
    {
        LastSession = Session;
        LastSpace = Space;
        SourceOrigin = Pelvis->GetTranslation();
        const FVector FlatRight(Right.X, Right.Y, 0.);
        if (FlatRight.IsNearlyZero())
            return;
        Alignment =
            Relative ? RefBasis * FRotationMatrix::MakeFromYZ(FlatRight, FVector::UpVector).ToQuat().Inverse()
                     : MeshToWorld.GetRotation().Inverse();
        FirstHead = Head ? Head->GetRotation() : FQuat::Identity;
    }
    static const FRigMap QuestMap(true), CameraMap(false);
    const auto &Map = Quest ? QuestMap : CameraMap;
    TArray<FTransform> Current;
    Current.SetNumUninitialized(Pose.GetNumBones());
    Pose.ResetToRefPose();
    for (const auto Index : Pose.ForEachBoneIndex())
    {
        const int32 I = Bones.MakeMeshPoseIndex(Index).GetInt();
        const int32 Parent = Pose.GetParentBoneIndex(Index).GetInt();
        const FTransform ParentCS = Parent < 0 ? FTransform::Identity : Current[Parent];
        FTransform CS = Pose[Index] * ParentCS;
        const FName Name = Ref.GetBoneName(I);
        if (Name == TEXT("pelvis"))
        {
            const FQuat Basis =
                FRotationMatrix::MakeFromYZ(Alignment.RotateVector(Right), Alignment.RotateVector(Up)).ToQuat();
            CS.SetRotation((Basis * RefBasis.Inverse() * Rest[I].GetRotation()).GetNormalized());
            CS.SetTranslation(Relative ? Rest[I].GetTranslation() +
                                             Alignment.RotateVector(Pelvis->GetTranslation() - SourceOrigin)
                                       : MeshToWorld.InverseTransformPosition(Pelvis->GetTranslation()));
        }
        else if (Name == TEXT("head") && Head)
        {
            const FQuat Delta = Alignment * Head->GetRotation() * FirstHead.Inverse() * Alignment.Inverse();
            CS.SetRotation((Delta * Rest[I].GetRotation()).GetNormalized());
        }
        else if (const FAim *A = Map.Aim.Find(Name))
        {
            const FTransform *From = Src.Find(A->Source), *To = Src.Find(A->End);
            const int32 Child = Ref.FindBoneIndex(A->TargetChild);
            const int32 Prev = Ref.FindBoneIndex(A->Previous);
            if (From && To && (Child >= 0 || Prev >= 0))
            {
                const FVector Direction = Child >= 0 ? Rest[Child].GetTranslation() - Rest[I].GetTranslation()
                                                     : Rest[I].GetTranslation() - Rest[Prev].GetTranslation();
                const FVector RestAxis = Rest[I].GetRotation().UnrotateVector(Direction).GetSafeNormal();
                const FVector Wanted =
                    Alignment.RotateVector(To->GetTranslation() - From->GetTranslation()).GetSafeNormal();
                if (!Wanted.IsNearlyZero() && !RestAxis.IsNearlyZero())
                    CS.SetRotation((FQuat::FindBetweenNormals(CS.GetRotation().RotateVector(RestAxis), Wanted) *
                                    CS.GetRotation())
                                       .GetNormalized());
            }
        }
        Pose[Index] = CS.GetRelativeTransform(ParentCS);
        Current[Index.GetInt()] = CS;
    }
}
