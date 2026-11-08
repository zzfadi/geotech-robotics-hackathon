#pragma once

#include "CoreMinimal.h"

namespace HumanTouchBench
{
constexpr double WidthCm = 28 * 2.54;
constexpr double LengthCm = 96 * 2.54;

// Keep the tabletop's top center and thickness while matching the measured footprint.
inline FTransform Tabletop(const FTransform &Original, const FVector &Lo, const FVector &Hi)
{
    FTransform Result = Original;
    const FVector Size = Hi - Lo;
    if (Size.X <= UE_SMALL_NUMBER || Size.Y <= UE_SMALL_NUMBER)
        return Result;
    const FVector TopCenter((Lo.X + Hi.X) * .5, (Lo.Y + Hi.Y) * .5, Hi.Z);
    Result.SetScale3D(FVector(WidthCm / Size.X, LengthCm / Size.Y, Original.GetScale3D().Z));
    Result.AddToTranslation(Original.TransformPosition(TopCenter) - Result.TransformPosition(TopCenter));
    return Result;
}
} // namespace HumanTouchBench
