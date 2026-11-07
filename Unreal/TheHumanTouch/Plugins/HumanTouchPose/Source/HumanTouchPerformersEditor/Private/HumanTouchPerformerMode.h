#pragma once
#include "CoreMinimal.h"

enum class EHumanTouchCast
{
    Mannequins,
    MetaHumans
};
EHumanTouchCast GetHumanTouchCast();
void SetHumanTouchCast(EHumanTouchCast Mode);
