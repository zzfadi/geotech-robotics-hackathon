#pragma once

#include "HumanTouchCornerSurvey.h"
class UWorld;

class FHumanTouchCornerCapture
{
  public:
    void Begin(int32 Table, const FString &Prefix, UWorld *World, bool bProbe);
    void Next();
    void BeginCheck(int32 Table, const FString &Prefix, UWorld *World, const FTransform &Room);
    bool CheckFailed = false;
    TOptional<FVector> AcceptedProbe;
    void Cancel(const FString &Reason);
    bool IsRunning() const
    {
        return TableNumber != INDEX_NONE;
    }
    // Returns true only once, after all independent checks pass.
    bool Tick(UWorld *World, int32 Table, bool bLive, FTransform &Room);
    static bool Geometry(UWorld *World, const FString &Prefix, TArray<FVector> &Targets, FString &Error);
    const TArray<FVector> &Targets() const
    {
        return Model;
    }
    const FString &Status() const
    {
        return Notice;
    }
    void ClearNotice()
    {
        Notice.Reset();
    }

  private:
    int32 TableNumber = INDEX_NONE;
    FString StationPrefix, Notice;
    TWeakObjectPtr<UWorld> SurveyWorld;
    TArray<FVector> Model, Points, Hold;
    FVector Repeated[2] = {FVector::ZeroVector, FVector::ZeroVector};
    int32 Step = 0;
    bool bPivot = false, bCheck = false;
    TArray<FTransform> PivotPoses;
    TOptional<FVector> ProbeTip;
    FTransform RoomToCheck;
    double StartAt = -1, LastSample = 0;
    void Prompt();
    void WriteReceipt(int32 Table, const HumanTouchCorners::FSolution &Result) const;
};
