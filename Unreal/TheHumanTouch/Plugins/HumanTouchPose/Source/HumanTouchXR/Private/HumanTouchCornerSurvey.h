#pragma once

#include "CoreMinimal.h"

namespace HumanTouchCorners
{
// World and tracking coordinates are Unreal centimetres. A..D are bare corner
// crosshairs, followed by three flange holes and the parked TCP on each arm.
constexpr int32 Count = 13;
constexpr int32 Floor = 12;
constexpr int32 Samples = Count + 2;
// Four plate holes set registration; every tabletop corner is independent.
constexpr int32 Order[Samples] = {4, 5, 6, 8, 9, 10, 0, 1, 2, 3, 12, 7, 11, 5, 8};
bool FitProbe(const TArray<FTransform> &Poses, FVector &Tip, FString &Error);
struct FSolution
{
    FTransform Room = FTransform::Identity;
    TArray<double> Errors;
    double MeasuredLength = 0;
    double MeasuredDepth = 0;
    FString Problem;
};
const TCHAR *Name(int32 Index);
bool Solve(const TArray<FVector> &Physical, const TArray<FVector> &Virtual, FSolution &Out);
} // namespace HumanTouchCorners
