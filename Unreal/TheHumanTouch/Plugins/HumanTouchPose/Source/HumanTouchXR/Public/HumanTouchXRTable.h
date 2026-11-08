#pragma once

#include "CoreMinimal.h"

namespace HumanTouchXRTable
{
    void Startup();
    void Shutdown();
    HUMANTOUCHXR_API int32 Number();
    HUMANTOUCHXR_API bool IsHidingOthers();
    HUMANTOUCHXR_API void HideOthers(bool bHide);
    HUMANTOUCHXR_API void AlignAfter(double Seconds);
    HUMANTOUCHXR_API FString Status();
}
