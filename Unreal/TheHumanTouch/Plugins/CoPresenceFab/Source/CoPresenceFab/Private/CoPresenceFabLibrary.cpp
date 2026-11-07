#include "CoPresenceFabLibrary.h"

#include "Async/Async.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Engine/Level.h"
#include "GameFramework/Actor.h"
#include "CoPresenceFabModule.h"
#include "Framework/Docking/TabManager.h"
#include "SWebBrowser.h"
#include "Styling/AppStyle.h"
#include "Widgets/Docking/SDockTab.h"

namespace UE::CoPresenceFab::Private
{
	// The Fab plugin names its tabs Fab1, Fab2 and so on, and makes its web browser a tab's
	// whole content. The newest open one is where it shows pages.
	constexpr int32 MaxFabTabNumber = 64;
	const FName WebBrowserWidgetType(TEXT("SWebBrowser"));

	TSharedPtr<SWebBrowser> FindFabBrowser()
	{
		const TSharedRef<FGlobalTabmanager>& TabManager = FGlobalTabmanager::Get();
		for (int32 TabNumber = MaxFabTabNumber; TabNumber >= 1; --TabNumber)
		{
			const FTabId TabId(FName(*FString::Printf(TEXT("Fab%d"), TabNumber)));
			const TSharedPtr<SDockTab> Tab = TabManager->FindExistingLiveTab(TabId);
			if (!Tab.IsValid())
			{
				continue;
			}

			const TSharedRef<SWidget> Content = Tab->GetContent();
			if (Content->GetType() == WebBrowserWidgetType)
			{
				return StaticCastSharedRef<SWebBrowser>(Content);
			}
		}
		return nullptr;
	}
}

void UCoPresenceFabChannel::Report(const FString& Json)
{
	if (IsInGameThread())
	{
		OnReport.Broadcast(Json);
		return;
	}

	// Python listens on the game thread
	AsyncTask(ENamedThreads::GameThread, [WeakChannel = TWeakObjectPtr<UCoPresenceFabChannel>(this), Json]()
	{
		if (UCoPresenceFabChannel* Channel = WeakChannel.Get())
		{
			Channel->OnReport.Broadcast(Json);
		}
	});
}

bool UCoPresenceFabLibrary::RunScriptInFabTab(const FString& Script)
{
	const TSharedPtr<SWebBrowser> Browser = UE::CoPresenceFab::Private::FindFabBrowser();
	if (!Browser.IsValid())
	{
		return false;
	}

	Browser->ExecuteJavascript(Script);
	return true;
}

FString UCoPresenceFabLibrary::GetFabTabUrl()
{
	const TSharedPtr<SWebBrowser> Browser = UE::CoPresenceFab::Private::FindFabBrowser();
	return Browser.IsValid() ? Browser->GetUrl() : FString();
}

UCoPresenceFabChannel* UCoPresenceFabLibrary::ConnectFabTab()
{
	const TSharedPtr<SWebBrowser> Browser = UE::CoPresenceFab::Private::FindFabBrowser();
	if (!Browser.IsValid())
	{
		return nullptr;
	}

	return FCoPresenceFabModule::Get().ConnectBrowser(Browser.ToSharedRef());
}

bool UCoPresenceFabLibrary::OpenDialog(UWidget* Content, const FText& Title)
{
	return FCoPresenceFabModule::Get().OpenDialog(Content, Title);
}

void UCoPresenceFabLibrary::CloseDialog(UWidget* Content)
{
	FCoPresenceFabModule::Get().CloseDialog(Content);
}

bool UCoPresenceFabLibrary::IsDialogOpen(UWidget* Content)
{
	return FCoPresenceFabModule::Get().IsDialogOpen(Content);
}

FCheckBoxStyle UCoPresenceFabLibrary::GetEditorCheckBoxStyle()
{
	return FAppStyle::Get().GetWidgetStyle<FCheckBoxStyle>("Checkbox");
}

void UCoPresenceFabLibrary::HoldBackNewAssets(const FString& Folder, bool bHoldBack)
{
	FCoPresenceFabModule::Get().HoldBackNewAssets(Folder, bHoldBack);
}

int32 UCoPresenceFabLibrary::DeleteHeldBackFiles(const FString& Folder)
{
	return FCoPresenceFabModule::Get().DeleteHeldBackFiles(Folder);
}

TArray<FString> UCoPresenceFabLibrary::TakeRefusedSaves()
{
	return FCoPresenceFabModule::Get().TakeRefusedSaves();
}

void UCoPresenceFabLibrary::PersistSessionChanges(bool bPublishMaps)
{
	FCoPresenceFabModule::Get().PersistSessionChanges(bPublishMaps);
}

bool UCoPresenceFabLibrary::PersistContentPackages(const TArray<FString>& PackageNames)
{
	return FCoPresenceFabModule::Get().PersistContentPackages(PackageNames);
}


bool UCoPresenceFabLibrary::PersistSoloPackages(const TArray<FString>& PackageNames)
{
	return FCoPresenceFabModule::Get().PersistSoloPackages(PackageNames);
}

TArray<FCoPresenceSharedPackage> UCoPresenceFabLibrary::SharePackages(const TArray<FString>& PackageNames)
{
	return FCoPresenceFabModule::Get().SharePackages(PackageNames);
}

int64 UCoPresenceFabLibrary::GetMaxSharedPackageBytes()
{
	return FCoPresenceFabModule::MaxSharedPackageBytes;
}

void UCoPresenceFabLibrary::QuitWithoutSaving()
{
	FPlatformMisc::RequestExit(false, TEXT("UCoPresenceFabLibrary::QuitWithoutSaving"));
}


bool UCoPresenceFabLibrary::RenameCurrentMapForCheckpoint(const FString& TargetPackage)
{
	return FCoPresenceFabModule::Get().RenameCurrentMapForCheckpoint(TargetPackage);
}


AActor* UCoPresenceFabLibrary::SpawnLocalPresence(UClass* ActorClass)
{
    UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
    // A local marker must never enroll the level in a user or Concert transaction.
    // Refuse this call during a transaction; the presence tick can try again later.
    if (!World || World->WorldType != EWorldType::Editor || GUndo || !ActorClass
        || !ActorClass->IsChildOf(AActor::StaticClass())
        || !ActorClass->HasAnyClassFlags(CLASS_Transient)
        || ActorClass->GetPathName() != TEXT("/CoPresence/BP_AgentGhost.BP_AgentGhost_C"))
    {
        return nullptr;
    }
    FActorSpawnParameters Parameters;
    Parameters.Name = FName(*(TEXT("CoPresenceSelf_") + FGuid::NewGuid().ToString(EGuidFormats::Digits)));
    Parameters.ObjectFlags = RF_Transient | RF_DuplicateTransient;
    Parameters.bTemporaryEditorActor = true;
    Parameters.bHideFromSceneOutliner = true;
    Parameters.bCreateActorPackage = false;
    Parameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    return World->SpawnActor<AActor>(ActorClass, FVector(-200, -200, 300), FRotator::ZeroRotator, Parameters);
}

bool UCoPresenceFabLibrary::DestroyLocalPresence(AActor* Actor)
{
    if (GUndo || !IsValid(Actor) || !Actor->HasAnyFlags(RF_Transient)
        || !Actor->GetName().StartsWith(TEXT("CoPresenceSelf_"))
        || !Actor->GetWorld() || Actor->GetWorld()->WorldType != EWorldType::Editor)
    {
        return false;
    }
    return Actor->GetWorld()->DestroyActor(Actor, false, false);
}
