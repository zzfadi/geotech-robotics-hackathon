#include "HumanTouchRelock.h"
#include "Algo/AllOf.h"
#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "HumanTouchBench.h"
#include "HumanTouchCornerSurvey.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
namespace
{
FString Dir()
{
    return FPaths::ProjectSavedDir() / TEXT("Capture/Relock");
}
using FJson = TSharedPtr<FJsonObject>;
FJson Read(const FString &Path)
{
    FString Text;
    FJson Value;
    if (IFileManager::Get().FileSize(*Path) > 16384 || !FFileHelper::LoadFileToString(Text, *Path))
        return {};
    if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Value))
        return {};
    return Value;
}
bool Write(const FString &Path, const TSharedRef<FJsonObject> &Value)
{
    FString Text;
    FJsonSerializer::Serialize(Value, TJsonWriterFactory<>::Create(&Text));
    IFileManager::Get().MakeDirectory(*Dir(), true);
    const FString Tmp = Path + TEXT(".tmp");
    return FFileHelper::SaveStringToFile(Text, *Tmp, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM) &&
           IFileManager::Get().Move(*Path, *Tmp, true);
}
TArray<TSharedPtr<FJsonValue>> Numbers(std::initializer_list<double> Values)
{
    TArray<TSharedPtr<FJsonValue>> Result;
    for (double V : Values)
        Result.Add(MakeShared<FJsonValueNumber>(V));
    return Result;
}
bool PoseFields(const FJson &Obj, FTransform &Out)
{
    double X, Y, Z, A;
    if (!Obj || !Obj->TryGetNumberField(TEXT("x"), X) || !Obj->TryGetNumberField(TEXT("y"), Y) ||
        !Obj->TryGetNumberField(TEXT("z"), Z) || !Obj->TryGetNumberField(TEXT("yaw"), A) || !FMath::IsFinite(X) ||
        !FMath::IsFinite(Y) || !FMath::IsFinite(Z) || !FMath::IsFinite(A))
        return false;
    Out = FTransform(FRotator(0, A, 0), FVector(X, Y, Z));
    return true;
}
void PutPose(const TSharedRef<FJsonObject> &Obj, const FTransform &T)
{
    const auto P = T.GetLocation();
    Obj->SetNumberField(TEXT("x"), P.X);
    Obj->SetNumberField(TEXT("y"), P.Y);
    Obj->SetNumberField(TEXT("z"), P.Z);
    Obj->SetNumberField(TEXT("yaw"), T.Rotator().Yaw);
}
bool AcceptCandidate(const FJson &C, const FString &Id, int32 Revision, double Now, const FTransform &Last,
                     const TArray<FVector> &Model, FTransform &Out)
{
    FString Token;
    double Rev, Time, Left, Right;
    FTransform Candidate;
    if (!C || !C->TryGetStringField(TEXT("epoch"), Token) || Token != Id ||
        !C->TryGetNumberField(TEXT("revision"), Rev) || Rev != Revision || !C->TryGetNumberField(TEXT("utc"), Time) ||
        !FMath::IsFinite(Time) || Now < Time || Now - Time > 1.5 || !C->TryGetNumberField(TEXT("left_utc"), Left) ||
        !FMath::IsFinite(Left) || !C->TryGetNumberField(TEXT("right_utc"), Right) || !FMath::IsFinite(Right) ||
        Now - FMath::Min(Left, Right) > 12 || FMath::Max(Left, Right) > Time || !PoseFields(C, Candidate) ||
        !FHumanTouchRelock::Bounded(Last, Candidate, Model))
        return false;
    Out = Candidate;
    return true;
}
bool InitialEdgePose(const FTransform &Head, const FTransform &Frame, double ModelFloorZ, FTransform &Out,
                     FString &Note)
{
    // First use has no measured shell-to-tracking-center transform. Preserve floor-level height and seed the edge.
    const double AboveTop = Head.GetLocation().Z - (Frame.GetLocation().Z - ModelFloorZ);
    if (AboveTop < 0 || AboveTop > 25 || FMath::Abs(Head.Rotator().Pitch) > 35 || FMath::Abs(Head.Rotator().Roll) > 15)
    {
        Note = TEXT(
            "Headset is outside the expected tabletop rest pose. Check the floor origin or use a measured offset.");
        return false;
    }
    Out = FHumanTouchRelock::YawPose(Head).Inverse() * FTransform(FVector(0, 0, AboveTop)) * Frame;
    Note = TEXT("Edge seed applied from the tracked floor. Shell offset is provisional until measured or taught.");
    return true;
}
} // namespace
void FHumanTouchRelock::Enable(bool bOn, bool bAllowCorrections)
{
    bEnabled = bOn;
    bWritable = bOn && bAllowCorrections;
    History.Reset();
    NextOutput = 0;
    IFileManager::Get().Delete(*(Dir() / TEXT("state.json")), false, true);
    IFileManager::Get().Delete(*(Dir() / TEXT("candidate.json")), false, true);
    if (Epoch.IsValid())
    {
        Epoch = FGuid::NewGuid();
        Revision = 0;
    }
}
void FHumanTouchRelock::Invalidate()
{
    if (Epoch.IsValid())
    {
        Epoch.Invalidate();
        History.Reset();
        Revision = 0;
        IFileManager::Get().Delete(*(Dir() / TEXT("state.json")), false, true);
    }
    bDockSeed = false;
}
void FHumanTouchRelock::Seed(bool bFromDock)
{
    Epoch = FGuid::NewGuid();
    Revision = 0;
    bDockSeed = bFromDock;
    History.Reset();
    NextOutput = 0;
}
FTransform FHumanTouchRelock::YawPose(const FTransform &T)
{
    return FTransform(FRotator(0, T.Rotator().Yaw, 0), T.GetLocation());
}
void FHumanTouchRelock::Sample(double Now, const FTransform &Head)
{
    if (!History.IsEmpty() && (Now - History.Last().Stamp > .15 || Now <= History.Last().Stamp))
        History.Reset();
    if (!History.IsEmpty() && Now - History.Last().Stamp < .015)
        return;
    History.Add({Now, Head});
    while (!History.IsEmpty() && History[0].Stamp < Now - 3.)
        History.RemoveAt(0, 1, EAllowShrinking::No);
}
bool FHumanTouchRelock::Bounded(const FTransform &Last, const FTransform &Candidate, const TArray<FVector> &Model)
{
    if (Model.Num() != HumanTouchCorners::Count || Candidate.ContainsNaN() || Last.ContainsNaN() ||
        !Candidate.GetScale3D().Equals(FVector::OneVector, .0001) ||
        Last.GetRotation().AngularDistance(Candidate.GetRotation()) > FMath::DegreesToRadians(2.5))
        return false;
    for (int32 I : {4, 8})
        if (FVector::Dist(Candidate.TransformPosition(Last.InverseTransformPosition(Model[I])), Model[I]) > 4.)
            return false;
    return true;
}
bool FHumanTouchRelock::Exchange(double Now, int32 Table, const TArray<FVector> &Model, const FTransform &Last,
                                 FTransform &Out, FString &Note)
{
    if (!bEnabled || !Epoch.IsValid() || Model.Num() != HumanTouchCorners::Count || Now < NextOutput)
        return false;
    NextOutput = Now + .10;
    auto State = MakeShared<FJsonObject>();
    const FString Id = Epoch.ToString();
    State->SetStringField(TEXT("epoch"), Id);
    State->SetNumberField(TEXT("revision"), Revision);
    State->SetNumberField(TEXT("table"), Table);
    State->SetBoolField(TEXT("allow_correction"), bWritable);
    State->SetNumberField(TEXT("editor_pid"), FPlatformProcess::GetCurrentProcessId());
    State->SetNumberField(TEXT("utc"), Now);
    State->SetStringField(TEXT("seed"), bDockSeed ? TEXT("dock") : TEXT("probe"));
    PutPose(State, Last);
    TArray<TSharedPtr<FJsonValue>> Marks, Poses;
    for (int32 I : {4, 5, 6, 8, 9, 10})
    {
        const auto &P = Model[I];
        Marks.Add(MakeShared<FJsonValueArray>(Numbers({P.X, P.Y, P.Z})));
    }
    for (const auto &Sample : History)
    {
        const auto P = Sample.Head.GetLocation();
        const auto Q = Sample.Head.GetRotation();
        Poses.Add(MakeShared<FJsonValueArray>(Numbers({Sample.Stamp, P.X, P.Y, P.Z, Q.X, Q.Y, Q.Z, Q.W})));
    }
    State->SetArrayField(TEXT("marks"), Marks);
    State->SetArrayField(TEXT("poses"), Poses);
    Write(Dir() / TEXT("state.json"), State);
    const FString Path = Dir() / TEXT("candidate.json");
    if (!IFileManager::Get().FileExists(*Path))
        return false;
    if (!bWritable)
    {
        IFileManager::Get().Delete(*Path);
        return false;
    }
    const FJson C = Read(Path);
    IFileManager::Get().Delete(*Path);
    if (!AcceptCandidate(C, Id, Revision, Now, Last, Model, Out))
    {
        Note = TEXT("Optical correction rejected. Holding the last alignment.");
        return false;
    }
    ++Revision;
    Note = TEXT("Both optical bases agree; local room corrected.");
    return true;
}
FTransform FHumanTouchRelock::DockFrame(const TArray<FVector> &M, bool bLongEdge)
{
    FVector Mid = (M[5] + M[6] + M[9] + M[10]) * .25;
    if (bLongEdge)
    {
        // Use the long edge opposite the mounts; keep heading perpendicular to the edge.
        const FVector AB = .5 * (M[0] + M[1]), CD = .5 * (M[2] + M[3]);
        const bool bStartAtCD = FVector::DistSquared2D(Mid, AB) <= FVector::DistSquared2D(Mid, CD);
        const FVector Facing = bStartAtCD ? AB - CD : CD - AB;
        return FTransform(FRotator(0, Facing.Rotation().Yaw, 0), bStartAtCD ? CD : AB);
    }
    Mid.Z = (M[0].Z + M[1].Z + M[2].Z + M[3].Z) * .25;
    return FTransform(FRotator(0, (M[3] - M[0]).Rotation().Yaw, 0), Mid);
}
bool FHumanTouchRelock::DockRequest(const TArray<FString> &Input, FString &Note)
{
    CancelDock();
    TArray<FString> Args = Input;
    bEdge = Args.IsEmpty() || Args[0] != TEXT("midpoint");
    if (!Args.IsEmpty() && (Args[0] == TEXT("edge") || Args[0] == TEXT("midpoint")))
        Args.RemoveAt(0);
    bFirstEdge =
        bEdge && Args.IsEmpty() && !IFileManager::Get().FileExists(*(Dir() / TEXT("dock-edge-v1-quest3.json")));
    if (Args.IsEmpty())
        Args = {TEXT("snap"), TEXT("quest3")};
    bManual = false;
    bLearn = false;
    if (Args.Num() == 1 && Args[0] == TEXT("cancel"))
    {
        Note = TEXT("Dock capture cancelled.");
        return false;
    }
    if (Args.Num() == 5 && Args[0] == TEXT("offset"))
    {
        double V[4];
        for (int I = 0; I < 4; ++I)
            if (!LexTryParseString(V[I], *Args[I + 1]) || !FMath::IsFinite(V[I]) ||
                FMath::Abs(V[I]) > (I == 3 ? 180 : 45))
            {
                Note = TEXT("Invalid measured dock offset (centimetres and yaw degrees).");
                return false;
            }
        if (V[2] <= 0)
        {
            Note = TEXT("Supply the measured positive height of the tracking center above the tabletop.");
            return false;
        }
        DockOffset = FTransform(FRotator(0, V[3], 0), FVector(V[0], V[1], V[2]));
        bManual = true;
    }
    else if (Args.Num() == 2 && (Args[0] == TEXT("learn") || Args[0] == TEXT("snap")))
    {
        if (Args[1].IsEmpty() || Args[1].Len() > 48 ||
            !Algo::AllOf(Args[1], [](TCHAR C) { return FChar::IsAlnum(C) || C == '-' || C == '_'; }))
        {
            Note = TEXT("Use a simple headset/cradle profile name.");
            return false;
        }
        DockProfile =
            Dir() / ((bEdge ? FString(TEXT("dock-edge-v1-")) : FString(TEXT("dock-"))) + Args[1] + TEXT(".json"));
        bLearn = Args[0] == TEXT("learn");
        if (!bLearn && !bFirstEdge)
        {
            const FJson P = Read(DockProfile);
            FString Datum;
            const bool bTaggedEdge =
                P && P->TryGetStringField(TEXT("dock_frame"), Datum) && Datum == TEXT("opposite-long-edge-v1");
            if (bTaggedEdge != bEdge || !PoseFields(P, DockOffset) || !P->TryGetNumberField(TEXT("pitch"), RestPitch) ||
                !P->TryGetNumberField(TEXT("roll"), RestRoll))
            {
                Note = TEXT("Dock profile missing. Learn it after probing, or use a measured offset.");
                return false;
            }
        }
    }
    else
    {
        Note = TEXT("HumanTouch.Dock [edge|midpoint] learn NAME | snap NAME | offset forward_cm right_cm up_cm yaw_deg "
                    "| cancel");
        return false;
    }
    History.Reset();
    DockAt = FDateTime::UtcNow().ToUnixTimestampDecimal() + 3;
    Note = bEdge ? TEXT("Rest the headset at the long-edge midpoint, rear shell flush, facing the arms. Keep it still.")
                 : TEXT("Rest the headset in its midpoint cradle facing across the table.");
    return true;
}
bool FHumanTouchRelock::DockTick(double Now, int32 Table, const TArray<FVector> &Model, const FTransform *SurveyRoom,
                                 FTransform &Out, FString &Note)
{
    if (DockAt < 0 || Now < DockAt)
        return false;
    if (Now > DockAt + 8)
    {
        CancelDock();
        Note = TEXT("Dock timed out without a stable tracked headset.");
        return false;
    }
    if (History.IsEmpty() || Now - History.Last().Stamp > .1 || Model.Num() != HumanTouchCorners::Count)
        return false;
    const FTransform &Head = History.Last().Head;
    double Span = 0;
    for (int32 I = History.Num() - 1; I >= 0; --I)
    {
        if (FVector::Dist(Head.GetLocation(), History[I].Head.GetLocation()) > .22 ||
            Head.GetRotation().AngularDistance(History[I].Head.GetRotation()) > FMath::DegreesToRadians(.35))
            break;
        Span = Now - History[I].Stamp;
        if (Span >= 1.25)
            break;
    }
    if (Span < 1.25)
        return false;
    CancelDock();
    const FTransform Frame = DockFrame(Model, bEdge);
    if (bLearn)
    {
        if (!SurveyRoom)
        {
            Note = TEXT("Learning requires a successful mechanical survey.");
            return false;
        }
        const FTransform Offset = (YawPose(Head) * (*SurveyRoom)).GetRelativeTransform(Frame);
        if (Offset.GetLocation().Size2D() > 22 || Offset.GetLocation().Z <= 0 || Offset.GetLocation().Z > 30 ||
            FMath::Abs(Offset.Rotator().Yaw) > 20)
        {
            Note = TEXT("Headset is outside the expected dock position or heading.");
            return false;
        }
        auto J = MakeShared<FJsonObject>();
        PutPose(J, Offset);
        J->SetStringField(TEXT("dock_frame"), bEdge ? TEXT("opposite-long-edge-v1") : TEXT("midpoint"));
        J->SetNumberField(TEXT("pitch"), Head.Rotator().Pitch);
        J->SetNumberField(TEXT("roll"), Head.Rotator().Roll);
        Note = Write(DockProfile, J) ? TEXT("Saved this local headset/cradle profile.")
                                     : TEXT("Dock profile write failed.");
        return false;
    }
    if (bFirstEdge)
    {
        return InitialEdgePose(Head, Frame, Model.Last().Z, Out, Note);
    }
    if (!bManual && (FMath::Abs(FMath::FindDeltaAngleDegrees(Head.Rotator().Pitch, RestPitch)) > 2 ||
                     FMath::Abs(FMath::FindDeltaAngleDegrees(Head.Rotator().Roll, RestRoll)) > 2))
    {
        Note = TEXT("Resting tilt does not match the learned dock.");
        return false;
    }
    Out = YawPose(Head).Inverse() * DockOffset * Frame;
    Note = FString::Printf(TEXT("Static %s alignment applied to table %d. Seed is provisional."),
                           bEdge ? TEXT("long-edge") : TEXT("midpoint"), Table);
    return true;
}

#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FHumanTouchOpticalRehearsalTest, "HumanTouch.XR.RelockAndDock",
                                 EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FHumanTouchOpticalRehearsalTest::RunTest(const FString &)
{
    TArray<FVector> Model;
    Model.Init(FVector::ZeroVector, HumanTouchCorners::Count);
    Model[0] = FVector(0, -100, 75);
    Model[1] = FVector(0, 100, 75);
    Model[2] = FVector(80, 100, 75);
    Model[3] = FVector(80, -100, 75);
    Model[5] = FVector(16, -31, 73);
    Model[6] = FVector(24, -49, 73);
    Model[9] = FVector(16, 49, 73);
    Model[10] = FVector(24, 31, 73);
    Model[4] = FVector(16, -49, 73);
    Model[8] = FVector(16, 31, 73);
    TestTrue(TEXT("Physical midpoint projects onto tabletop"),
             FHumanTouchRelock::DockFrame(Model).GetLocation().Equals(FVector(20, 0, 75)));
    const auto Edge = FHumanTouchRelock::DockFrame(Model, true);
    TestTrue(TEXT("Shell sits on opposite long edge, centered and facing arms"),
             Edge.GetLocation().Equals(FVector(80, 0, 75)) &&
                 Edge.GetRotation().GetForwardVector().Equals(-FVector::ForwardVector, .001));
    const FVector Lo(-70, -80, -1), Hi(30, 20, 1);
    const FTransform Original(FRotator(0, 35, 0), FVector(40, 80, 75), FVector(.8, 2, 1.5));
    const FTransform Bench = HumanTouchBench::Tabletop(Original, Lo, Hi);
    const FVector Center(-20, -30, 1);
    TestTrue(TEXT("Measured tabletop shares the authored top center"),
             Bench.TransformPosition(Center).Equals(Original.TransformPosition(Center), .001));
    TestTrue(TEXT("28 by 96 inches in rendered and surveyed frame"),
             FMath::IsNearlyEqual(100 * Bench.GetScale3D().X, 71.12, .001) &&
                 FMath::IsNearlyEqual(100 * Bench.GetScale3D().Y, 243.84, .001));
    FString Note;
    FTransform Room;
    const FTransform Head(FRotator(6, 64, -3), FVector(62, 84, 96));
    FTransform Initial;
    TestTrue(TEXT("First fixed edge placement preserves floor height and uses the actual midpoint"),
             InitialEdgePose(Head, Edge, 0, Initial, Note) &&
                 Initial.TransformPosition(Head.GetLocation()).Equals(FVector(80, 0, 96), .001));
    TestFalse(TEXT("First edge placement refuses a standing headset"),
              InitialEdgePose(FTransform(FVector(0, 0, 175)), Edge, 0, Initial, Note));
    FHumanTouchRelock R;
    TestFalse(TEXT("Fresh editor has no optical reads or corrections"), R.Enabled() || R.Writable());
    TestTrue(TEXT("Measured static offset accepted"),
             R.DockRequest({TEXT("midpoint"), TEXT("offset"), TEXT("2"), TEXT("1"), TEXT("8"), TEXT("0")}, Note));
    const double Now = FDateTime::UtcNow().ToUnixTimestampDecimal() + 3.3;
    R.Sample(Now, Head);
    TestFalse(TEXT("One frame cannot dock"), R.DockTick(Now, 1, Model, nullptr, Room, Note));
    for (int32 I = 1; I <= 80; ++I)
        R.Sample(Now + .02 * I, Head);
    TestTrue(TEXT("Stable placement succeeds"), R.DockTick(Now + 1.60, 1, Model, nullptr, Room, Note));
    TestTrue(TEXT("Eye above the midpoint with measured offsets"),
             Room.TransformPosition(Head.GetTranslation()).Equals(FVector(22, 1, 83), .001));
    TestTrue(TEXT("Rigid unit scale"), Room.GetScale3D().Equals(FVector::OneVector));
    const FString Profile = TEXT("test-") + FGuid::NewGuid().ToString();
    R.DockRequest({TEXT("midpoint"), TEXT("learn"), Profile}, Note);
    for (int32 I = 0; I <= 80; ++I)
        R.Sample(Now + .02 * I, Head);
    FTransform Unused;
    TestFalse(TEXT("Learning leaves room unchanged"), R.DockTick(Now + 1.60, 1, Model, &Room, Unused, Note));
    TestTrue(TEXT("Named cradle read back"), R.DockRequest({TEXT("midpoint"), TEXT("snap"), Profile}, Note));
    for (int32 I = 0; I <= 80; ++I)
        R.Sample(Now + .02 * I, Head);
    TestTrue(TEXT("Named cradle restores identical transform"),
             R.DockTick(Now + 1.60, 1, Model, nullptr, Unused, Note) && Room.Equals(Unused, .001));
    TestFalse(TEXT("Edge dock cannot reuse an incompatible midpoint profile"),
              R.DockRequest({TEXT("edge"), TEXT("snap"), Profile}, Note));
    TestTrue(TEXT("Measured rear-shell edge dock starts without optical updates"),
             R.DockRequest({TEXT("edge"), TEXT("offset"), TEXT("3"), TEXT("0"), TEXT("8"), TEXT("0")}, Note));
    for (int I = 0; I <= 80; ++I)
        R.Sample(Now + .02 * I, Head);
    TestTrue(TEXT("Rear-shell offset maps eye to edge and faces arms"),
             R.DockTick(Now + 1.6, 1, Model, nullptr, Unused, Note) &&
                 Unused.TransformPosition(Head.GetTranslation()).Equals(FVector(77, 0, 83), .001) && !R.Writable());
    IFileManager::Get().Delete(*(Dir() / (TEXT("dock-") + Profile + TEXT(".json"))));
    TestTrue(TEXT("Small translation"),
             FHumanTouchRelock::Bounded(FTransform::Identity, FTransform(FVector(1, 0, 0)), Model));
    TestFalse(TEXT("Large correction held"),
              FHumanTouchRelock::Bounded(FTransform::Identity, FTransform(FVector(5, 0, 0)), Model));
    TestFalse(TEXT("Large yaw held"),
              FHumanTouchRelock::Bounded(FTransform::Identity, FTransform(FRotator(0, 5, 0)), Model));
    const FString SessionId = FGuid::NewGuid().ToString();
    auto Candidate = MakeShared<FJsonObject>();
    PutPose(Candidate, FTransform(FVector(1, 0, 0)));
    Candidate->SetStringField(TEXT("epoch"), SessionId);
    Candidate->SetNumberField(TEXT("revision"), 0);
    Candidate->SetNumberField(TEXT("utc"), Now);
    Candidate->SetNumberField(TEXT("left_utc"), Now - 1);
    Candidate->SetNumberField(TEXT("right_utc"), Now - 2);
    TestTrue(TEXT("Candidate for both current bases accepted"),
             AcceptCandidate(Candidate, SessionId, 0, Now + .1, FTransform::Identity, Model, Unused));
    FTransform Held(FVector(0, 2, 0));
    TestFalse(TEXT("Replayed revision held"),
              AcceptCandidate(Candidate, SessionId, 1, Now, FTransform::Identity, Model, Held));
    TestFalse(TEXT("Recentered tracking space held"),
              AcceptCandidate(Candidate, TEXT("new"), 0, Now, FTransform::Identity, Model, Held));
    TestFalse(TEXT("Expired candidate held"),
              AcceptCandidate(Candidate, SessionId, 0, Now + 2, FTransform::Identity, Model, Held));
    Candidate->SetNumberField(TEXT("right_utc"), Now - 13);
    TestFalse(TEXT("One stale base is insufficient"),
              AcceptCandidate(Candidate, SessionId, 0, Now, FTransform::Identity, Model, Held));
    TestTrue(TEXT("All rejected updates preserve the output transform"), Held.Equals(FTransform(FVector(0, 2, 0))));
    return true;
}
#endif
