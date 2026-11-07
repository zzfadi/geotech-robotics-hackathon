#include "HumanTouchPoseSourceFactory.h"
#include "Common/UdpSocketBuilder.h"
#include "Common/UdpSocketReceiver.h"
#include "Dom/JsonObject.h"
#include "Features/IModularFeatures.h"
#include "HAL/IConsoleManager.h"
#include "ILiveLinkClient.h"
#include "ILiveLinkSource.h"
#include "Modules/ModuleManager.h"
#include "Roles/LiveLinkAnimationRole.h"
#include "Roles/LiveLinkAnimationTypes.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

DEFINE_LOG_CATEGORY_STATIC(LogHumanTouchPose, Log, All);

namespace
{
constexpr uint16 Port = 17860;
struct FPoseLayout
{
    TArray<FName> Names;
    TArray<int32> Parents;
    double LastStationTime = -1;
};

// The station relay uses right-handed metres (+X right, +Y up, -Z forward).
// Reflections act on quaternion vector components as det(B) * B.
FTransform ToUnreal(const double* Values)
{
    return FTransform(FQuat(Values[5], -Values[3], -Values[4], Values[6]).GetNormalized(),
                      FVector(-Values[2], Values[0], Values[1]) * 100.);
}

class FPoseSource final : public ILiveLinkSource
{
public:
    FPoseSource()
    {
        Socket = FUdpSocketBuilder(TEXT("HumanTouchPose"))
            .BoundToEndpoint(FIPv4Endpoint(FIPv4Address::InternalLoopback, Port))
            .AsNonBlocking().WithReceiveBufferSize(1024 * 1024);
    }
    ~FPoseSource() { RequestSourceShutdown(); }

    void ReceiveClient(ILiveLinkClient* InClient, FGuid InGuid) override
    {
        Client = InClient;
        Guid = InGuid;
        if (Socket)
        {
            Receiver = MakeUnique<FUdpSocketReceiver>(Socket, FTimespan::FromMilliseconds(2), TEXT("HumanTouchPose UDP"));
            Receiver->OnDataReceived().BindRaw(this, &FPoseSource::OnPacket);
            Receiver->Start();
        }
    }
    bool RequestSourceShutdown() override
    {
        if (Receiver)
        {
            Receiver->Stop();
            Receiver.Reset();
        }
        if (Socket)
        {
            ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Socket);
            Socket = nullptr;
        }
        return true;
    }
    bool IsSourceStillValid() const override { return Socket != nullptr; }
    FText GetSourceType() const override { return FText::FromString(TEXT("Human Touch Pose")); }
    FText GetSourceMachineName() const override { return FText::FromString(TEXT("127.0.0.1:17860")); }
    FText GetSourceStatus() const override { return FText::FromString(Socket ? TEXT("Listening") : TEXT("Port unavailable")); }

private:
    void OnPacket(const FArrayReaderPtr& Packet, const FIPv4Endpoint&)
    {
        if (Packet->IsEmpty() || Packet->Num() > 60000) return;
        FUTF8ToTCHAR Text(reinterpret_cast<const UTF8CHAR*>(Packet->GetData()), Packet->Num());
        TSharedPtr<FJsonObject> Json;
        if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(FString(Text.Length(), Text.Get())), Json) || !Json) return;
        FString Subject, Space, Session, StationNs, Sequence;
        double Version = 0;
        const TArray<TSharedPtr<FJsonValue>>* Bones = nullptr;
        if (!Json->TryGetNumberField(TEXT("version"), Version) || Version != 1 ||
            !Json->TryGetStringField(TEXT("subject"), Subject) ||
            (Subject != TEXT("Human_Cam_B") && Subject != TEXT("Human_Quest_A")) ||
            !Json->TryGetStringField(TEXT("space"), Space) || Space.IsEmpty() ||
            !Json->TryGetStringField(TEXT("session"), Session) || Session.IsEmpty() ||
            !Json->TryGetStringField(TEXT("seq"), Sequence) || !Sequence.IsNumeric() ||
            !Json->TryGetStringField(TEXT("station_ns"), StationNs) || !StationNs.IsNumeric() ||
            !Json->TryGetArrayField(TEXT("bones"), Bones) || Bones->Num() < 2 || Bones->Num() > 100) return;
        const double StationTime = FCString::Atod(*StationNs) * 1.e-9;
        if (!FMath::IsFinite(StationTime) || StationTime <= 0) return;
        FPoseLayout& Layout = Layouts.FindOrAdd(FName(*Subject));
        if (StationTime <= Layout.LastStationTime) return;
        TArray<FName> Names;
        TArray<int32> Parents;
        TArray<FTransform> World;
        FLiveLinkFrameDataStruct Frame(FLiveLinkAnimationFrameData::StaticStruct());
        FLiveLinkAnimationFrameData* Data = Frame.Cast<FLiveLinkAnimationFrameData>();
        for (const auto& Joint : *Bones)
        {
            const TArray<TSharedPtr<FJsonValue>>* Row = nullptr;
            if (!Joint->TryGetArray(Row) || Row->Num() != 10) return;
            FString Name;
            double Parent;
            if (!(*Row)[0]->TryGetString(Name) || Name.IsEmpty() || Name.Len() > 64 ||
                !(*Row)[1]->TryGetNumber(Parent) || !FMath::IsFinite(Parent) ||
                Parent != FMath::FloorToDouble(Parent) || Parent >= Names.Num() ||
                Parent < (Names.IsEmpty() ? -1 : 0)) return;
            double V[8];
            for (int32 I = 0; I < 8; ++I)
            {
                if (!(*Row)[I + 2]->TryGetNumber(V[I]) || !FMath::IsFinite(V[I])) return;
            }
            if (V[7] < 0 || V[7] > 1 || FMath::Abs(V[0]) > 100 || FMath::Abs(V[1]) > 100 || FMath::Abs(V[2]) > 100 ||
                !FMath::IsNearlyEqual(FQuat(V[3], V[4], V[5], V[6]).SizeSquared(), 1., 0.02)) return;
            const FName BoneName(*Name);
            if (Names.Contains(BoneName)) return;
            Names.Add(BoneName);
            Parents.Add(static_cast<int32>(Parent));
            const FTransform Transform = ToUnreal(V);
            Data->Transforms.Add(Parent < 0 ? Transform : Transform.GetRelativeTransform(World[Parents.Last()]));
            Data->PropertyValues.Add(static_cast<float>(V[7]));
            World.Add(Transform);
        }
        const FLiveLinkSubjectKey Key(Guid, FName(*Subject));
        if (Layout.Names != Names || Layout.Parents != Parents)
        {
            FLiveLinkStaticDataStruct Static(FLiveLinkSkeletonStaticData::StaticStruct());
            auto* Skeleton = Static.Cast<FLiveLinkSkeletonStaticData>();
            Skeleton->SetBoneNames(Names);
            Skeleton->SetBoneParents(Parents);
            for (FName Name : Names) Skeleton->PropertyNames.Add(FName(*(Name.ToString() + TEXT("_confidence"))));
            Client->PushSubjectStaticData_AnyThread(Key, ULiveLinkAnimationRole::StaticClass(), MoveTemp(Static));
            UE_LOG(LogHumanTouchPose, Display, TEXT("Registered %s: %d bones"), *Subject, Names.Num());
            Layout.Names = MoveTemp(Names);
            Layout.Parents = MoveTemp(Parents);
        }
        Layout.LastStationTime = StationTime;
        Data->WorldTime = FLiveLinkWorldTime(StationTime);
        Data->MetaData.StringMetaData.Add(TEXT("station_ns"), StationNs);
        Data->MetaData.StringMetaData.Add(TEXT("seq"), Sequence);
        Data->MetaData.StringMetaData.Add(TEXT("session"), Session);
        Data->MetaData.StringMetaData.Add(TEXT("space"), Space);
        Client->PushSubjectFrameData_AnyThread(Key, MoveTemp(Frame));
    }

    FSocket* Socket = nullptr;
    TUniquePtr<FUdpSocketReceiver> Receiver;
    ILiveLinkClient* Client = nullptr;
    FGuid Guid;
    TMap<FName, FPoseLayout> Layouts;
};

ILiveLinkClient* FindClient()
{
    auto& Features = IModularFeatures::Get();
    return Features.IsModularFeatureAvailable(ILiveLinkClient::ModularFeatureName)
        ? &Features.GetModularFeature<ILiveLinkClient>(ILiveLinkClient::ModularFeatureName) : nullptr;
}

FAutoConsoleCommand Listen(TEXT("HumanTouch.Pose.Listen"), TEXT("Add the Human Touch Pose Live Link source."),
    FConsoleCommandDelegate::CreateLambda([]
    {
        auto Source = MakeShared<FPoseSource>();
        if (auto* Client = FindClient(); Client && Source->IsSourceStillValid())
        {
            Client->AddSource(Source);
            UE_LOG(LogHumanTouchPose, Display, TEXT("Listening on 127.0.0.1:%d"), Port);
        }
    }));

FAutoConsoleCommand Inspect(TEXT("HumanTouch.Pose.Inspect"), TEXT("Evaluate the two Animation subjects and log the frame IDs."),
    FConsoleCommandDelegate::CreateLambda([]
    {
        if (auto* Client = FindClient())
        {
            for (const TCHAR* Subject : {TEXT("Human_Cam_B"), TEXT("Human_Quest_A")})
            {
                FLiveLinkSubjectFrameData Sample;
                if (!Client->EvaluateFrame_AnyThread(FName(Subject), ULiveLinkAnimationRole::StaticClass(), Sample)) continue;
                const auto* Pose = Sample.FrameData.Cast<FLiveLinkAnimationFrameData>();
                const auto* Rig = Sample.StaticData.Cast<FLiveLinkSkeletonStaticData>();
                if (!Rig || !Pose || Pose->Transforms.IsEmpty()) continue;
                const auto& Tags = Pose->MetaData.StringMetaData;
                UE_LOG(LogHumanTouchPose, Display, TEXT("Evaluated %s: %d bones, last=%s, session=%s seq=%s station_ns=%s space=%s"),
                    Subject, Pose->Transforms.Num(), *Rig->GetBoneNames().Last().ToString(), *Tags.FindRef(TEXT("session")),
                    *Tags.FindRef(TEXT("seq")), *Tags.FindRef(TEXT("station_ns")), *Tags.FindRef(TEXT("space")));
            }
        }
    }));
}

FText UHumanTouchPoseSourceFactory::GetSourceDisplayName() const { return FText::FromString(TEXT("Human Touch Pose")); }
FText UHumanTouchPoseSourceFactory::GetSourceTooltip() const { return FText::FromString(TEXT("Listen for local station skeletons on UDP 17860")); }
TSharedPtr<ILiveLinkSource> UHumanTouchPoseSourceFactory::CreateSource(const FString&) const
{
    auto Source = MakeShared<FPoseSource>();
    if (!Source->IsSourceStillValid()) return nullptr;
    return Source;
}
