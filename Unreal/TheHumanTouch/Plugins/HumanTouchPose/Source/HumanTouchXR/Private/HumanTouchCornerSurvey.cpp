#include "HumanTouchCornerSurvey.h"
#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#endif

namespace HumanTouchCorners
{
const TCHAR *Name(int32 Index)
{
    static const TCHAR *const Names[Count] = {TEXT("A near left"),
                                              TEXT("B near right"),
                                              TEXT("C far right"),
                                              TEXT("D far left"),
                                              TEXT("L1 near-left flange hole"),
                                              TEXT("L2 near-right flange hole"),
                                              TEXT("L3 far-left flange hole"),
                                              TEXT("L4 parked grasp-center witness"),
                                              TEXT("R1 near-left flange hole"),
                                              TEXT("R2 near-right flange hole"),
                                              TEXT("R3 far-left flange hole"),
                                              TEXT("R4 parked grasp-center witness"),
                                              TEXT("floor beside A (height only)")};
    return Names[Index];
}

bool FitProbe(const TArray<FTransform> &Poses, FVector &Tip, FString &Error)
{
    // Fit t_i + R_i * tip = pivot with an unknown fixed pivot. Subtracting the
    // means eliminates the pivot. The last two poses are never used to fit.
    constexpr int32 N = 8;
    if (Poses.Num() != N + 2)
    {
        Error = TEXT("Collect ten pivot orientations.");
        return false;
    }
    FVector Mean = FVector::ZeroVector,
            AxisMeans[3] = {FVector::ZeroVector, FVector::ZeroVector, FVector::ZeroVector};
    const EAxis::Type Axes[] = {EAxis::X, EAxis::Y, EAxis::Z};
    for (int32 I = 0; I < N; ++I)
    {
        Mean += Poses[I].GetTranslation() / N;
        for (int32 J = 0; J < 3; ++J)
            AxisMeans[J] += Poses[I].GetUnitAxis(Axes[J]) / N;
    }
    FVector Rows[3] = {FVector::ZeroVector, FVector::ZeroVector, FVector::ZeroVector}, B = FVector::ZeroVector;
    for (int32 I = 0; I < N; ++I)
    {
        FVector Col[3];
        for (int32 J = 0; J < 3; ++J)
            Col[J] = Poses[I].GetUnitAxis(Axes[J]) - AxisMeans[J];
        for (int32 J = 0; J < 3; ++J)
        {
            B[J] += FVector::DotProduct(Col[J], Mean - Poses[I].GetTranslation());
            for (int32 K = 0; K < 3; ++K)
                Rows[J][K] += FVector::DotProduct(Col[J], Col[K]);
        }
    }
    const FVector C0 = FVector::CrossProduct(Rows[1], Rows[2]);
    const double Det = FVector::DotProduct(Rows[0], C0);
    const double MeanDiag = (Rows[0].X + Rows[1].Y + Rows[2].Z) / 3;
    if (MeanDiag < 0.35 || Det < 0.06 * FMath::Pow(MeanDiag, 3.))
    {
        Error = TEXT("Pivot through wider angles about two different axes.");
        return false;
    }
    Tip = (B.X * C0 + B.Y * FVector::CrossProduct(Rows[2], Rows[0]) +
           B.Z * FVector::CrossProduct(Rows[0], Rows[1])) /
          Det;
    FVector Pivot = FVector::ZeroVector;
    for (int32 I = 0; I < N; ++I)
        Pivot += Poses[I].TransformPosition(Tip) / N;
    for (const auto &Pose : Poses)
        if (Tip.ContainsNaN() || Tip.Size() > 45 || FVector::Dist(Pose.TransformPosition(Tip), Pivot) > 0.3)
        {
            Error = TEXT("Probe pivot exceeded 3 mm or the tip offset was invalid. Reseat the tip and repeat.");
            return false;
        }
    return true;
}

bool Solve(const TArray<FVector> &Physical, const TArray<FVector> &Virtual, FSolution &Out)
{
    Out = FSolution();
    if (Physical.Num() != Count || Virtual.Num() != Count)
    {
        Out.Problem = TEXT("Table, floor and both sets of robot landmarks are required.");
        return false;
    }
    for (int32 I = 0; I < Count; ++I)
        if (Physical[I].ContainsNaN() || Virtual[I].ContainsNaN())
        {
            Out.Problem = TEXT("Non-finite landmark.");
            return false;
        }

    // Fit only four factory holes spanning both stationary mounts. Two other
    // holes, four tabletop corners, and both parked TCPs never influence it.
    constexpr int32 Fit[] = {4, 6, 9, 10};
    FVector P = FVector::ZeroVector, V = FVector::ZeroVector;
    for (const int32 I : Fit)
    {
        P += Physical[I] / 4.;
        V += Virtual[I] / 4.;
    }
    double Dot = 0, Cross = 0;
    for (const int32 I : Fit)
    {
        const FVector A = Physical[I] - P, B = Virtual[I] - V;
        Dot += A.X * B.X + A.Y * B.Y;
        Cross += A.X * B.Y - A.Y * B.X;
    }
    if (FMath::Abs(FVector::CrossProduct(Physical[6] - Physical[4], Physical[9] - Physical[4]).Z) < 100 ||
        FMath::Abs(FVector::CrossProduct(Virtual[6] - Virtual[4], Virtual[9] - Virtual[4]).Z) < 100 ||
        FMath::IsNearlyZero(FMath::Abs(Dot) + FMath::Abs(Cross)))
    {
        Out.Problem = TEXT("Mount references are repeated or nearly collinear. Check the flange hole order.");
        return false;
    }
    const FRotator Rotation(0, FMath::RadiansToDegrees(FMath::Atan2(Cross, Dot)), 0);
    Out.Room = FTransform(Rotation, V - Rotation.RotateVector(P));
    Out.MeasuredLength =
        (FVector::Distance(Physical[0], Physical[1]) + FVector::Distance(Physical[3], Physical[2])) / 2;
    Out.MeasuredDepth =
        (FVector::Distance(Physical[0], Physical[3]) + FVector::Distance(Physical[1], Physical[2])) / 2;

    // Both diagonals and all four sides must fit, with no estimated scale.
    for (int32 I = 0; I < 4; ++I)
        for (int32 J = I + 1; J < 4; ++J)
        {
            const double Expected = FVector::Distance(Virtual[I], Virtual[J]);
            const double Actual = FVector::Distance(Physical[I], Physical[J]);
            if (Out.Problem.IsEmpty() && FMath::Abs(Actual - Expected) > FMath::Min(1.0, Expected * 0.01))
                Out.Problem = FString::Printf(TEXT("Table %c-%c measured %.1f cm, model %.1f cm. Check corner "
                                                   "identity and the modeled tabletop dimensions."),
                                              'A' + I, 'A' + J, Actual, Expected);
        }
    for (int32 I = 0; I < Count; ++I)
    {
        const FVector Projected = Out.Room.TransformPosition(Physical[I]);
        const double Error =
            I == Floor ? FMath::Abs(Projected.Z - Virtual[I].Z) : FVector::Dist(Projected, Virtual[I]);
        Out.Errors.Add(Error);
        const double Limit = I < 4 || I == 7 || I == 11 || I == Floor ? 1.0 : 0.5;
        if (Out.Problem.IsEmpty() && Error > Limit)
            Out.Problem = FString::Printf(
                TEXT("%s missed by %.1f cm (limit %.1f cm). Check the marked point, mounting or parked pose."),
                Name(I), Error, Limit);
    }
    const double MeasuredSpan = FVector::Dist(Physical[4], Physical[8]);
    const double ModelSpan = FVector::Dist(Virtual[4], Virtual[8]);
    if (Out.Problem.IsEmpty() && FMath::Abs(MeasuredSpan - ModelSpan) > 0.5)
        Out.Problem =
            FString::Printf(TEXT("Inter-arm L1-R1 distance differs by %.1f mm."), 10 * (MeasuredSpan - ModelSpan));
    for (const int32 I : {4, 8})
        if (Out.Problem.IsEmpty() && FVector::Dist2D(Virtual[I + 3], (Virtual[I + 1] + Virtual[I + 2]) * 0.5) < 12)
            Out.Problem = TEXT(
                "Park each tool at the matching asymmetric pose, at least 12 cm horizontally from the base axis.");
    // A symmetric flange can be bolted on backwards. Check the long bolt
    // baseline direction as well as the asymmetric, parked tool witness.
    for (int32 First : {4, 8})
    {
        const FVector Measured = Out.Room.TransformVector(Physical[First + 1] - Physical[First]).GetSafeNormal();
        const FVector Expected = (Virtual[First + 1] - Virtual[First]).GetSafeNormal();
        const double Angle =
            FMath::RadiansToDegrees(FMath::Acos(FMath::Clamp(FVector::DotProduct(Measured, Expected), -1., 1.)));
        if (Out.Problem.IsEmpty() && Angle > 2)
            Out.Problem = FString::Printf(TEXT("%s bolt-row direction differs by %.1f degrees (limit 2)."),
                                          Name(First), Angle);
    }
    return Out.Problem.IsEmpty();
}
} // namespace HumanTouchCorners

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FHumanTouchCornerSurveyTest, "HumanTouch.XR.CornerSurvey",
                                 EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FHumanTouchCornerSurveyTest::RunTest(const FString &)
{
    using namespace HumanTouchCorners;
    const TArray<FVector> V = {{-40, -100, 75}, {-40, 100, 75}, {40, 100, 75}, {40, -100, 75}, {-20, -49, 75},
                               {-20, -31, 75},  {-12, -49, 75}, {15, -38, 90}, {-20, 31, 75},  {-20, 49, 75},
                               {-12, 31, 75},   {15, 42, 90},   {-40, -100, 0}};
    const FTransform Known(FRotator(0, 114, 0), FVector(982, -79, -51));
    TArray<FVector> P;
    for (const FVector &Point : V)
        P.Add(Known.InverseTransformPosition(Point));
    FSolution S;
    TestTrue(TEXT("Exact whole-station survey"), Solve(P, V, S));
    TestTrue(TEXT("Recovers XYZ and yaw at unit scale"), S.Room.Equals(Known, 1.e-5));
    TestTrue(TEXT("Recovers outside dimensions"),
             FMath::IsNearlyEqual(S.MeasuredLength, 200.) && FMath::IsNearlyEqual(S.MeasuredDepth, 80.));
    auto Bad = P;
    Bad[3].Z += 2;
    TestFalse(TEXT("Held-out corner"), Solve(Bad, V, S));
    TestTrue(TEXT("Held-out point never participates in fit"), S.Room.Equals(Known, 1.e-5));
    Bad = P;
    for (auto &Point : Bad)
        Point *= 1.03;
    TestFalse(TEXT("Physical scale mismatch"), Solve(Bad, V, S));
    Bad = P;
    Swap(Bad[0], Bad[1]);
    TestFalse(TEXT("Wrong corner labels"), Solve(Bad, V, S));
    Bad = P;
    Bad[4].Z += 1.1;
    TestFalse(TEXT("Left mount"), Solve(Bad, V, S));
    Bad = P;
    Bad[11].Z += 2;
    TestFalse(TEXT("Right tool pose"), Solve(Bad, V, S));
    Bad = P;
    Bad[Floor].Z += 2;
    TestFalse(TEXT("Floor elevation"), Solve(Bad, V, S));
    Bad = P;
    Bad[2] = Bad[1];
    TestFalse(TEXT("Repeated point"), Solve(Bad, V, S));
    TArray<FTransform> Poses;
    const FVector Tip(4, -2, 15), Pivot(38, -60, 70);
    for (int32 I = 0; I < 10; ++I)
    {
        FRotator R((I % 3 - 1) * 31., I * 18., (I % 4) * 14.);
        Poses.Emplace(R, Pivot - R.RotateVector(Tip));
    }
    FVector Estimated;
    FString Error;
    TestTrue(TEXT("Probe pivot with independent checks"),
             FitProbe(Poses, Estimated, Error) && Estimated.Equals(Tip, 1.e-5));
    Poses.Last().AddToTranslation(FVector(1, 0, 0));
    TestFalse(TEXT("Held-out pivot slip"), FitProbe(Poses, Estimated, Error));
    for (auto &Pose : Poses)
        Pose = FTransform::Identity;
    TestFalse(TEXT("Degenerate pivot"), FitProbe(Poses, Estimated, Error));
    return true;
}
#endif
