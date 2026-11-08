#pragma once
#include "CoreMinimal.h"

// Optical worker only exchanges local, short-lived observations for this editor epoch.
class FHumanTouchRelock
{
  public:
    void Enable(bool bOn, bool bAllowCorrections = false);
    bool Writable() const
    {
        return bWritable;
    }
    bool Enabled() const
    {
        return bEnabled;
    }
    void Invalidate();
    void Seed(bool bFromDock);
    bool DockSeed() const
    {
        return bDockSeed;
    }
    void Sample(double Now, const FTransform &Head);
    bool Exchange(double Now, int32 Table, const TArray<FVector> &Model, const FTransform &Last, FTransform &Out,
                  FString &Note);
    bool DockRequest(const TArray<FString> &Args, FString &Note);
    bool DockPending() const
    {
        return DockAt > 0;
    }
    void CancelDock()
    {
        DockAt = -1;
    }
    bool DockTick(double Now, int32 Table, const TArray<FVector> &Model, const FTransform *SurveyRoom, FTransform &Out,
                  FString &Note);
    static FTransform DockFrame(const TArray<FVector> &Model, bool bLongEdge = false);
    static FTransform YawPose(const FTransform &Pose);
    static bool Bounded(const FTransform &Last, const FTransform &Candidate, const TArray<FVector> &Model);

  private:
    struct FPose
    {
        double Stamp;
        FTransform Head;
    };
    TArray<FPose> History;
    FGuid Epoch;
    int32 Revision = 0;
    bool bEnabled = false, bWritable = false, bDockSeed = false;
    double NextOutput = 0, DockAt = -1;
    FString DockProfile;
    bool bLearn = false, bManual = false, bEdge = false, bFirstEdge = false;
    FTransform DockOffset;
    double RestPitch = 0, RestRoll = 0;
};
