#pragma once

#include "CoreMinimal.h"

namespace HumanTouchXRTable
{
    void Startup();
    void Shutdown();
    HUMANTOUCHXR_API int32 Number();
    HUMANTOUCHXR_API bool IsHidingOthers();
    HUMANTOUCHXR_API void HideOthers(bool bHide);
    HUMANTOUCHXR_API void Dock(const TArray<FString>& Args);
    HUMANTOUCHXR_API void AlignAfter(double Seconds);
    HUMANTOUCHXR_API void BeginSurvey(bool bProbe = true);
    HUMANTOUCHXR_API bool CanCheckSurvey();
    HUMANTOUCHXR_API void CheckSurvey();
    HUMANTOUCHXR_API void CaptureMark();
    HUMANTOUCHXR_API void CancelSurvey();
    HUMANTOUCHXR_API bool SurveyRunning();
    HUMANTOUCHXR_API FString Status();
}
