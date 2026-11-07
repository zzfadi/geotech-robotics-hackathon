#include "Common/UdpSocketBuilder.h"
#include "Dom/JsonObject.h"
#include "Features/IModularFeatures.h"
#include "HAL/IConsoleManager.h"
#include "ILiveLinkClient.h"
#include "Modules/ModuleManager.h"
#include "Roles/LiveLinkAnimationRole.h"
#include "Roles/LiveLinkAnimationTypes.h"
#include "Serialization/JsonSerializer.h"

DEFINE_LOG_CATEGORY_STATIC(LogHubPoseExport, Log, All);

namespace
{
constexpr int32 Port = 17866;

bool IsPerformer(FName Name)
{
    return Name == TEXT("Human_Quest_A") || Name == TEXT("Human_Cam_B") || Name == TEXT("Human_Quest_C");
}

// Export component-space transforms before avatar retargeting. UE centimetres, +Y right
// become right-handed metres, +Y left. A reflection negates the axial quaternion's X and Z.
bool MakePacket(const FLiveLinkSubjectKey& Key, const FLiveLinkSkeletonStaticData& Rig,
                const FLiveLinkAnimationFrameData& Frame, const FString& Session,
                const FString& Sequence, const FString& Stamp, const FString& Space,
                const TSharedRef<FJsonObject>& Evaluation, FString& Out)
{
    const TArray<FName>& Names = Rig.GetBoneNames();
    const TArray<int32>& Parents = Rig.GetBoneParents();
    if (Names.IsEmpty() || Names.Num() > 100 || Names.Num() != Parents.Num() ||
        Names.Num() != Frame.Transforms.Num())
    {
        return false;
    }

    TArray<TSharedPtr<FJsonValue>> Bones;
    TArray<FTransform> ComponentSpace;
    for (int32 Index = 0; Index < Names.Num(); ++Index)
    {
        const int32 Parent = Parents[Index];
        if ((Index == 0 && Parent != INDEX_NONE) || (Index > 0 && (Parent < 0 || Parent >= Index)))
        {
            return false;
        }
        const FTransform Transform = Parent == INDEX_NONE ? Frame.Transforms[Index]
                                                           : Frame.Transforms[Index] * ComponentSpace[Parent];
        if (Transform.ContainsNaN() || !Transform.GetScale3D().Equals(FVector::OneVector))
        {
            return false;
        }
        const FString Name = Names[Index].ToString();
        const int32 ConfidenceIndex = Rig.PropertyNames.IndexOfByKey(FName(*(Name + TEXT("_confidence"))));
        if (!Frame.PropertyValues.IsValidIndex(ConfidenceIndex))
        {
            return false;
        }
        ComponentSpace.Add(Transform);
        const FVector P = Transform.GetTranslation() * .01;
        const FQuat Q = Transform.GetRotation().GetNormalized();
        TArray<TSharedPtr<FJsonValue>> Row;
        Row.Add(MakeShared<FJsonValueString>(Name));
        for (double V : { static_cast<double>(Parent), P.X, -P.Y, P.Z, -Q.X, Q.Y, -Q.Z, Q.W,
                          static_cast<double>(Frame.PropertyValues[ConfidenceIndex]) })
        {
            Row.Add(MakeShared<FJsonValueNumber>(V));
        }
        Bones.Add(MakeShared<FJsonValueArray>(Row));
    }
    auto Packet = MakeShared<FJsonObject>();
    Packet->SetStringField(TEXT("type"), TEXT("hub_evaluated_pose_v2"));
    Packet->SetObjectField(TEXT("evaluation"), Evaluation);
    Packet->SetStringField(TEXT("subject"), Key.SubjectName.ToString());
    auto Reference = MakeShared<FJsonObject>();
    Reference->SetStringField(TEXT("session"), Session);
    Reference->SetStringField(TEXT("seq"), Sequence);
    Reference->SetStringField(TEXT("station_ns"), Stamp);
    Packet->SetObjectField(TEXT("capture_ref"), Reference);
    Packet->SetStringField(TEXT("space"), Space);
    Packet->SetArrayField(TEXT("bones"), MoveTemp(Bones));
    return FJsonSerializer::Serialize(Packet, TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out));
}
}

class FHubPoseExportModule : public IModuleInterface
{
public:
    void StartupModule() override
    {
        StartCommand = IConsoleManager::Get().RegisterConsoleCommand(
            TEXT("HumanTouch.Isaac.Start"), TEXT("Send station editor Hub poses to loopback UDP 17866"),
            FConsoleCommandDelegate::CreateRaw(this, &FHubPoseExportModule::Start));
        StopCommand = IConsoleManager::Get().RegisterConsoleCommand(
            TEXT("HumanTouch.Isaac.Stop"), TEXT("Stop exporting Hub poses"),
            FConsoleCommandDelegate::CreateRaw(this, &FHubPoseExportModule::Stop));
    }

    void ShutdownModule() override
    {
        Stop();
        IConsoleManager::Get().UnregisterConsoleObject(StartCommand);
        IConsoleManager::Get().UnregisterConsoleObject(StopCommand);
    }

private:
    ILiveLinkClient* Client() const
    {
        auto& Features = IModularFeatures::Get();
        return Features.IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName)
            ? &Features.GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName) : nullptr;
    }

    void Start()
    {
        if (Socket) return;
        ILiveLinkClient* LiveLink = Client();
        if (!LiveLink) { UE_LOG(LogHubPoseExport, Error, TEXT("Live Link client unavailable")); return; }
        Socket = FUdpSocketBuilder(TEXT("HubPoseExport")).AsNonBlocking().WithSendBufferSize(1 << 20);
        if (!Socket) { UE_LOG(LogHubPoseExport, Error, TEXT("Could not open loopback sender")); return; }
        ExportSession = FGuid::NewGuid().ToString(EGuidFormats::Digits);
        EvaluationTick = 0;
        TickHandle = LiveLink->OnLiveLinkTicked().AddRaw(this, &FHubPoseExportModule::Publish);
        UE_LOG(LogHubPoseExport, Display, TEXT("Exporting enabled Live Link Hub performers to 127.0.0.1:%d"), Port);
    }

    void Stop()
    {
        if (ILiveLinkClient* LiveLink = Client())
        {
            LiveLink->OnLiveLinkTicked().Remove(TickHandle);
        }
        TickHandle.Reset();
        if (Socket)
        {
            ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Socket);
            Socket = nullptr;
        }
    }

    void Publish()
    {
        ILiveLinkClient* LiveLink = Client();
        if (!Socket || !LiveLink) return;
        // On Mac, Cycles64 and Python's monotonic clock both use mach_absolute_time.
        // FPlatformTime::Seconds() adds an engine debug offset; it is a different epoch.
        const uint64 StationNs = static_cast<uint64>(FPlatformTime::ToSeconds64(FPlatformTime::Cycles64()) * 1.e9);
        auto Evaluation = MakeShared<FJsonObject>();
        Evaluation->SetStringField(TEXT("session"), ExportSession);
        Evaluation->SetStringField(TEXT("tick"), LexToString(++EvaluationTick));
        Evaluation->SetStringField(TEXT("station_ns"), LexToString(StationNs));
        const auto Endpoint = FIPv4Endpoint(FIPv4Address::InternalLoopback, Port).ToInternetAddr();
        for (const FLiveLinkSubjectKey& Key : LiveLink->GetSubjects(false, true))
        {
            const FName Name = Key.SubjectName.Name;
            if (!IsPerformer(Name) || LiveLink->GetSourceType(Key.Source).ToString() != TEXT("Live Link Hub")) continue;
            FLiveLinkSubjectFrameData Data;
            if (!LiveLink->EvaluateFrame_AnyThread(Key.SubjectName, ULiveLinkAnimationRole::StaticClass(), Data)) continue;
            const auto* Rig = Data.StaticData.Cast<FLiveLinkSkeletonStaticData>();
            const auto* Frame = Data.FrameData.Cast<FLiveLinkAnimationFrameData>();
            if (!Rig || !Frame) continue;
            const auto& Meta = Frame->MetaData.StringMetaData;
            const FString Stamp = Meta.FindRef(TEXT("station_ns")), Seq = Meta.FindRef(TEXT("seq"));
            const FString Session = Meta.FindRef(TEXT("session")), Space = Meta.FindRef(TEXT("space"));
            if (!Stamp.IsNumeric() || !Seq.IsNumeric() || Session.IsEmpty() || Space.IsEmpty()) continue;
            // The editor can hold and evaluate a disconnected subject indefinitely.
            const int64 CaptureAge = static_cast<int64>(StationNs) - FCString::Atoi64(*Stamp);
            if (CaptureAge < -50000000 || CaptureAge > 250000000) continue;
            FString Packet;
            if (!MakePacket(Key, *Rig, *Frame, Session, Seq, Stamp, Space, Evaluation, Packet)) continue;
            FTCHARToUTF8 Body(*Packet);
            int32 Sent = 0;
            if (Body.Length() <= 60000)
            {
                Socket->SendTo(reinterpret_cast<const uint8*>(Body.Get()), Body.Length(), Sent, *Endpoint);
            }
        }
    }

    FSocket* Socket = nullptr;
    FString ExportSession;
    uint64 EvaluationTick = 0;
    FDelegateHandle TickHandle;
    IConsoleObject* StartCommand = nullptr;
    IConsoleObject* StopCommand = nullptr;
};

IMPLEMENT_MODULE(FHubPoseExportModule, HubPoseExport)
