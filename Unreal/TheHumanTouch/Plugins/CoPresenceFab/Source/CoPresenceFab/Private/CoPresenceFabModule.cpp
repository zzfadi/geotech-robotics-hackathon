#include "CoPresenceFabModule.h"

#include "Components/Widget.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Editor.h"
#include "Engine/World.h"
#include "ConcertMessageData.h"
#include "FileHelpers.h"
#include "ConcertWorkspaceData.h"
#include "CoPresenceFabLibrary.h"
#include "Engine/Texture.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Docking/TabManager.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "IConcertClientPackageBridge.h"
#include "IConcertClientWorkspace.h"
#include "IConcertSyncClient.h"
#include "IConcertSyncClientModule.h"
#include "IMultiUserClientModule.h"
#include "Misc/CommandLine.h"
#include "Misc/CoreDelegates.h"
#include "Misc/PackageName.h"
#include "Misc/PackagePath.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "SWebBrowser.h"
#include "UObject/Package.h"
#include "UObject/UObjectHash.h"
#include "Widgets/SWindow.h"

DEFINE_LOG_CATEGORY_STATIC(LogCoPresenceFab, Log, All);

namespace UE::CoPresenceFab::Private
{
	// The page finds the channel as window.ue.copresence
	const TCHAR* const ChannelName = TEXT("copresence");
	const FName ModuleName(TEXT("CoPresenceFab"));
	const FName PackageFilterName(TEXT("CoPresenceFab"));
	// Tools/copresence.sh and Tools/join-mac.sh start agents' editors with -COPRESENCEAGENT
	const TCHAR* const AgentEditorSwitch = TEXT("COPRESENCEAGENT");

	/** The slash keeps /Game/Fab from also matching /Game/FabTwo */
	FString AsFolderPath(const FString& Folder)
	{
		return Folder.EndsWith(TEXT("/")) ? Folder : Folder + TEXT("/");
	}

	/** Where a package's file is in the project folder itself, outside Multi-User's sandbox. */
	FString GetProjectFilename(const FConcertPackageInfo& PackageInfo)
	{
		FString Filename;
		if (!FPackageName::TryConvertLongPackageNameToFilename(PackageInfo.PackageName.ToString(), Filename, PackageInfo.PackageFileExtension))
		{
			return FString();
		}
		return FPaths::ConvertRelativePathToFull(Filename);
	}

	/** The editor's Multi-User client, or null when Multi-User is off. */
	TSharedPtr<IConcertSyncClient> GetMultiUserClient()
	{
		return IMultiUserClientModule::IsAvailable() ? IMultiUserClientModule::Get().GetClient() : nullptr;
	}

	/** A loaded package of the project's own content, or null. Never loads one as a side effect. */
	UPackage* FindContentPackage(const FString& PackageName)
	{
		if (!PackageName.StartsWith(TEXT("/Game/")) || !FPackageName::IsValidLongPackageName(PackageName))
		{
			return nullptr;
		}
		UPackage* Package = FindPackage(nullptr, *PackageName);
		if (!Package || Package->HasAnyFlags(RF_Transient) || Package->HasAnyPackageFlags(PKG_CompiledIn))
		{
			return nullptr;
		}
		return Package;
	}

	/** Packages a level keeps its actors in, one per actor */
	bool IsLevelActorPackage(const UPackage& Package)
	{
		const FString PackageName = Package.GetName();
		return Package.HasAnyPackageFlags(PKG_ContainsMapData)
			|| PackageName.Contains(FPackagePath::GetExternalActorsFolderName())
			|| PackageName.Contains(FPackagePath::GetExternalObjectsFolderName());
	}

	/**
	 * Roughly how big a package's file will be once saved, before saving it. A texture saves
	 * its source image; other heavy assets save about what they take up in memory.
	 */
	int64 EstimatePackageBytes(const UPackage& Package)
	{
		int64 Bytes = 0;
		ForEachObjectWithPackage(&Package, [&Bytes](UObject* Object)
		{
			if (const UTexture* Texture = Cast<UTexture>(Object))
			{
				Bytes += Texture->Source.GetSizeOnDisk();
			}
			else
			{
				Bytes += static_cast<int64>(Object->GetResourceSizeBytes(EResourceSizeMode::EstimatedTotal));
			}
			return true;
		});
		return Bytes;
	}

	/** Size of a saved package's file; in a session the file manager reads it from the sandbox */
	int64 GetPackageFileBytes(const FString& PackageName)
	{
		FString Filename;
		if (!FPackageName::TryConvertLongPackageNameToFilename(PackageName, Filename, FPackageName::GetAssetPackageExtension()))
		{
			return 0;
		}
		return FMath::Max<int64>(IFileManager::Get().FileSize(*Filename), 0);
	}
}

FCoPresenceFabModule& FCoPresenceFabModule::Get()
{
	return FModuleManager::LoadModuleChecked<FCoPresenceFabModule>(UE::CoPresenceFab::Private::ModuleName);
}

UCoPresenceFabChannel* FCoPresenceFabModule::ConnectBrowser(const TSharedRef<SWebBrowser>& Browser)
{
	if (!Channel.IsValid())
	{
		Channel.Reset(NewObject<UCoPresenceFabChannel>());
	}

	if (ConnectedBrowser.Pin().Get() != &Browser.Get())
	{
		Browser->BindUObject(UE::CoPresenceFab::Private::ChannelName, Channel.Get(), true);
		// A page only sees the objects that were bound before it loaded
		Browser->Reload();
		ConnectedBrowser = Browser;
	}

	return Channel.Get();
}

bool FCoPresenceFabModule::OpenDialog(UWidget* Content, const FText& Title)
{
	if (!Content || !FSlateApplication::IsInitialized())
	{
		return false;
	}

	CloseDialog(Content);

	const TSharedRef<SWindow> Window = SNew(SWindow)
		.Title(Title)
		.SizingRule(ESizingRule::Autosized)
		.AutoCenter(EAutoCenter::PreferredWorkArea)
		.SupportsMinimize(false)
		.SupportsMaximize(false)
		[
			Content->TakeWidget()
		];

	// The module can go away before the window does when the editor shuts down
	Window->SetOnWindowClosed(FOnWindowClosed::CreateLambda([](const TSharedRef<SWindow>& ClosedWindow)
	{
		if (FCoPresenceFabModule* Module = FModuleManager::GetModulePtr<FCoPresenceFabModule>(UE::CoPresenceFab::Private::ModuleName))
		{
			Module->HandleDialogClosed(ClosedWindow);
		}
	}));
	Dialogs.Add({TStrongObjectPtr<UWidget>(Content), Window});

	// A child of the main window stays in front of it
	const TSharedPtr<SWindow> MainWindow = FGlobalTabmanager::Get()->GetRootWindow();
	if (MainWindow.IsValid())
	{
		FSlateApplication::Get().AddWindowAsNativeChild(Window, MainWindow.ToSharedRef());
	}
	else
	{
		FSlateApplication::Get().AddWindow(Window);
	}
	return true;
}

void FCoPresenceFabModule::CloseDialog(const UWidget* Content)
{
	const int32 Index = Dialogs.IndexOfByPredicate([Content](const FDialog& Dialog)
	{
		return Dialog.Content.Get() == Content;
	});
	if (Index == INDEX_NONE)
	{
		return;
	}

	const TSharedPtr<SWindow> Window = Dialogs[Index].Window.Pin();
	Dialogs.RemoveAt(Index);
	if (Window.IsValid())
	{
		Window->RequestDestroyWindow();
	}
}

bool FCoPresenceFabModule::IsDialogOpen(const UWidget* Content) const
{
	return Dialogs.ContainsByPredicate([Content](const FDialog& Dialog)
	{
		return Dialog.Content.Get() == Content && Dialog.Window.IsValid();
	});
}

void FCoPresenceFabModule::HandleDialogClosed(const TSharedRef<SWindow>& Window)
{
	Dialogs.RemoveAll([&Window](const FDialog& Dialog)
	{
		return Dialog.Window.HasSameObject(&Window.Get());
	});
}

void FCoPresenceFabModule::HoldBackNewAssets(const FString& Folder, bool bHoldBack)
{
	const FString FolderPath = UE::CoPresenceFab::Private::AsFolderPath(Folder);
	if (bHoldBack)
	{
		HeldBackFolders.AddUnique(FolderPath);
	}
	else
	{
		HeldBackFolders.Remove(FolderPath);
		// Files of assets that weren't saved stay, since they are those assets' only copy on disk
		for (auto It = HeldBackFiles.CreateIterator(); It; ++It)
		{
			if (It.Key().ToString().StartsWith(FolderPath))
			{
				It.RemoveCurrent();
			}
		}
	}
	UpdatePackageFilter();
}

int32 FCoPresenceFabModule::DeleteHeldBackFiles(const FString& Folder)
{
	const FString FolderPath = UE::CoPresenceFab::Private::AsFolderPath(Folder);
	// The physical file system goes around Multi-User's sandbox, which holds the saved assets
	IPlatformFile& PhysicalFile = IPlatformFile::GetPlatformPhysical();
	int32 DeletedFiles = 0;
	for (auto It = HeldBackFiles.CreateIterator(); It; ++It)
	{
		const FString PackageName = It.Key().ToString();
		if (!PackageName.StartsWith(FolderPath))
		{
			continue;
		}

		const UPackage* Package = FindPackage(nullptr, *PackageName);
		if (!Package || Package->IsDirty())
		{
			continue;
		}

		const FString& Filename = It.Value();
		if (PhysicalFile.FileExists(*Filename))
		{
			if (PhysicalFile.DeleteFile(*Filename))
			{
				++DeletedFiles;
			}
			else
			{
				UE_LOG(LogCoPresenceFab, Warning, TEXT("Couldn't delete %s, which Multi-User wrote while holding back %s"), *Filename, *PackageName);
			}
		}
		It.RemoveCurrent();
	}
	return DeletedFiles;
}

EPackageFilterResult FCoPresenceFabModule::FilterPackage(const FConcertPackageInfo& PackageInfo)
{
	// Multi-User sends a full copy of each new asset as soon as it is created, and an
	// Interchange import announces each asset twice. Only those copies wait; saves,
	// renames and deletes go through as usual.
	if (PackageInfo.PackageUpdateType != EConcertPackageUpdateType::Added)
	{
		return EPackageFilterResult::UseDefault;
	}

	const FString PackageName = PackageInfo.PackageName.ToString();
	const bool bInHeldBackFolder = HeldBackFolders.ContainsByPredicate([&PackageName](const FString& FolderPath)
	{
		return PackageName.StartsWith(FolderPath);
	});
	if (!bInHeldBackFolder)
	{
		return EPackageFilterResult::UseDefault;
	}

	// Multi-User writes each package it holds back into the project folder itself. One that
	// is there already, for example from git, is sent as usual so its file stays untouched.
	if (!HeldBackFiles.Contains(PackageInfo.PackageName))
	{
		const FString ProjectFilename = UE::CoPresenceFab::Private::GetProjectFilename(PackageInfo);
		if (ProjectFilename.IsEmpty() || IPlatformFile::GetPlatformPhysical().FileExists(*ProjectFilename))
		{
			return EPackageFilterResult::UseDefault;
		}
		HeldBackFiles.Add(PackageInfo.PackageName, ProjectFilename);
	}
	return EPackageFilterResult::Exclude;
}

void FCoPresenceFabModule::UpdatePackageFilter()
{
	// Multi-User isn't loaded when its plugin is off, and may already be gone on shutdown
	if (!IConcertSyncClientModule::IsAvailable())
	{
		return;
	}

	const bool bWantsFilter = !HeldBackFolders.IsEmpty();
	if (bWantsFilter == bPackageFilterRegistered)
	{
		return;
	}

	IConcertClientPackageBridge& PackageBridge = IConcertSyncClientModule::Get().GetPackageBridge();
	if (bWantsFilter)
	{
		PackageBridge.RegisterPackageFilter(UE::CoPresenceFab::Private::PackageFilterName,
			FPackageFilterDelegate::CreateRaw(this, &FCoPresenceFabModule::FilterPackage));
	}
	else
	{
		PackageBridge.UnregisterPackageFilter(UE::CoPresenceFab::Private::PackageFilterName);
	}
	bPackageFilterRegistered = bWantsFilter;
}

TArray<FString> FCoPresenceFabModule::TakeRefusedSaves()
{
	return MoveTemp(RefusedSaves);
}

void FCoPresenceFabModule::PersistSessionChanges(bool bPublishMaps)
{
	const TSharedPtr<IConcertSyncClient> Client = UE::CoPresenceFab::Private::GetMultiUserClient();
	if (!Client.IsValid())
	{
		UE_LOG(LogCoPresenceFab, Warning, TEXT("Multi-User isn't running, so there is no session to persist"));
		return;
	}

	if (!Client->GetWorkspace().IsValid())
	{
		return;
	}
	// UE 5.8's local dummy save events are excluded by the unmuted-activity
	// query used by GatherSessionChanges. Publish an ordinary map save first:
	// its real package activity covers the live transactions on every client.
	// Keep this inside the explicit persistence guard; ordinary agent saves
	// remain forbidden. Only loaded maps already pending in this workspace qualify.
	TArray<UPackage*> Maps;
	for (const FName Name : bPublishMaps ? Client->GetWorkspace()->GatherSessionChanges() : TArray<FName>())
	{
		UPackage* Package = FindPackage(nullptr, *Name.ToString());
		if (Package && Package->HasAnyPackageFlags(PKG_ContainsMap))
		{
			Maps.AddUnique(Package);
		}
	}
	TGuardValue<bool> Persisting(bPersisting, true);
	if (!Maps.IsEmpty() && !UEditorLoadingAndSavingUtils::SavePackages(Maps, false))
	{
		UE_LOG(LogCoPresenceFab, Error, TEXT("Map publication failed; persistence cancelled"));
		return;
	}
	Client->PersistAllSessionChanges();
}

bool FCoPresenceFabModule::PersistContentPackages(const TArray<FString>& PackageNames)
{
	const TSharedPtr<IConcertSyncClient> Client = UE::CoPresenceFab::Private::GetMultiUserClient();
	if (!Client.IsValid() || !Client->GetWorkspace().IsValid() || PackageNames.IsEmpty())
	{
		return false;
	}
	TArray<UPackage*> Packages;
	TArray<FName> Names;
	// Validate the entire selection before any write. Never load assets as a side effect.
	for (const FString& Name : PackageNames)
	{
		UPackage* Package = UE::CoPresenceFab::Private::FindContentPackage(Name);
		if (!Package || Package->HasAnyPackageFlags(PKG_ContainsMap | PKG_InMemoryOnly))
		{
			UE_LOG(LogCoPresenceFab, Error, TEXT("Invalid content persistence selection: %s"), *Name);
			return false;
		}
		Packages.AddUnique(Package);
		Names.AddUnique(FName(*Name));
	}
	TGuardValue<bool> Persisting(bPersisting, true);
	if (!UEditorLoadingAndSavingUtils::SavePackages(Packages, true))
	{
		return false;
	}
	const FPersistResult Result = Client->GetWorkspace()->PersistSessionChanges({ Names });
	return Result.PersistStatus == EPersistStatus::Success;
}

bool FCoPresenceFabModule::PersistSoloPackages(const TArray<FString>& PackageNames)
{
	const TSharedPtr<IConcertSyncClient> Client = UE::CoPresenceFab::Private::GetMultiUserClient();
	if (!FParse::Param(FCommandLine::Get(), TEXT("COPRESENCESOLO"))
		|| (Client.IsValid() && Client->GetWorkspace().IsValid()))
	{
		UE_LOG(LogCoPresenceFab, Error, TEXT("Solo persistence requires solo mode without a Concert workspace"));
		return false;
	}
	// PKG_InMemoryOnly includes PKG_NewlyCreated: new project assets must be saveable here.
	TArray<UPackage*> Packages;
	for (const FString& Name : PackageNames)
	{
		UPackage* Package = UE::CoPresenceFab::Private::FindContentPackage(Name);
		if (!Package)
		{
			UE_LOG(LogCoPresenceFab, Error, TEXT("Invalid solo persistence selection: %s"), *Name);
			return false;
		}
		Packages.AddUnique(Package);
	}
	if (Packages.IsEmpty()) return true;
	// The explicit host operation uses the same scoped permission as Concert persistence.
	// Ordinary package saves remain refused before and after this call.
	TGuardValue<bool> Persisting(bPersisting, true);
	return UEditorLoadingAndSavingUtils::SavePackages(Packages, true);
}

TArray<FCoPresenceSharedPackage> FCoPresenceFabModule::SharePackages(const TArray<FString>& PackageNames)
{
	const TSharedPtr<IConcertSyncClient> Client = UE::CoPresenceFab::Private::GetMultiUserClient();
	const TSharedPtr<IConcertClientWorkspace> Workspace = Client.IsValid() ? Client->GetWorkspace() : nullptr;

	TArray<FCoPresenceSharedPackage> Results;
	Results.Reserve(PackageNames.Num());
	for (const FString& PackageName : PackageNames)
	{
		FCoPresenceSharedPackage& Shared = Results.AddDefaulted_GetRef();
		Shared.PackageName = PackageName;
		UPackage* Package = UE::CoPresenceFab::Private::FindContentPackage(PackageName);
		// Saving outside a session would write straight into the project's files
		Shared.Problem = Workspace.IsValid() ? GetShareProblem(PackageName, Package, *Workspace)
			: TEXT("There's no Multi-User session to share it with");
		if (!Shared.Problem.IsEmpty())
		{
			UE_LOG(LogCoPresenceFab, Warning, TEXT("Didn't share %s: %s"), *PackageName, *Shared.Problem);
			continue;
		}
		if (!Package->IsDirty())
		{
			Shared.Result = ECoPresenceShareResult::Unchanged;
			continue;
		}

		// One package at a time, so the save check lets exactly this one through
		bool bSaved = false;
		{
			TGuardValue<FName> Sharing(SharingPackage, Package->GetFName());
			bSaved = UEditorLoadingAndSavingUtils::SavePackages({ Package }, true);
		}
		// Multi-User's own check, in front of this module's, refuses packages others hold
		if (!bSaved || Package->IsDirty())
		{
			Shared.Problem = TEXT("Multi-User didn't let it be saved; someone else may be editing it");
			UE_LOG(LogCoPresenceFab, Warning, TEXT("Didn't share %s: %s"), *PackageName, *Shared.Problem);
			continue;
		}
		Shared.Result = ECoPresenceShareResult::Shared;
		Shared.SizeBytes = UE::CoPresenceFab::Private::GetPackageFileBytes(PackageName);
		if (Shared.SizeBytes > MaxSharedPackageBytes)
		{
			UE_LOG(LogCoPresenceFab, Warning, TEXT("Shared %s, whose file came out at %lld bytes, over the %lld bytes its estimate allowed"),
				*PackageName, Shared.SizeBytes, MaxSharedPackageBytes);
		}
	}
	return Results;
}

FString FCoPresenceFabModule::GetShareProblem(const FString& PackageName, UPackage* Package, IConcertClientWorkspace& Workspace) const
{
	if (!Package)
	{
		return TEXT("It isn't a loaded content package under /Game");
	}
	// Level edits reach everyone as they happen, and the session is persisted for everyone at once
	if (Package->HasAnyPackageFlags(PKG_ContainsMap) || UE::CoPresenceFab::Private::IsLevelActorPackage(*Package))
	{
		return TEXT("Levels and the actors in them reach everyone through their edits, not by saving");
	}

	const FName PackageFName = Package->GetFName();
	const FGuid LockOwner = Workspace.GetResourceLockId(PackageFName);
	if (LockOwner.IsValid() && LockOwner != Workspace.GetWorkspaceLockId())
	{
		return TEXT("Someone else is editing it");
	}
	TArray<FConcertClientInfo> OtherClients;
	if (Workspace.IsAssetModifiedByOtherClients(PackageFName, nullptr, &OtherClients, 1))
	{
		const FString Who = OtherClients.IsEmpty() ? FString(TEXT("Someone else")) : OtherClients[0].DisplayName;
		return FString::Printf(TEXT("%s has unsaved changes in it"), *Who);
	}

	const int64 EstimatedBytes = UE::CoPresenceFab::Private::EstimatePackageBytes(*Package);
	if (EstimatedBytes > MaxSharedPackageBytes)
	{
		constexpr int64 BytesPerMegabyte = 1024 * 1024;
		return FString::Printf(TEXT("At about %lld MB it's over the %lld MB a shared asset can have; bigger content goes through git"),
			EstimatedBytes / BytesPerMegabyte, MaxSharedPackageBytes / BytesPerMegabyte);
	}
	return FString();
}

void FCoPresenceFabModule::RefuseSaves()
{
	// Multi-User puts its lock check in front of this one while it is in a session, passes
	// on the saves it allows, and puts this one back when it leaves
	PreviousSaveCheck = FCoreUObjectDelegates::IsPackageOKToSaveDelegate;
	FCoreUObjectDelegates::IsPackageOKToSaveDelegate.BindLambda([](UPackage* Package, const FString& Filename, FOutputDevice* Error)
	{
		// Multi-User can still hold this check after the module has shut down
		FCoPresenceFabModule* Module = FModuleManager::GetModulePtr<FCoPresenceFabModule>(UE::CoPresenceFab::Private::ModuleName);
		return !Module || Module->CanSavePackage(Package, Filename, Error);
	});
	SaveCheckHandle = FCoreUObjectDelegates::IsPackageOKToSaveDelegate.GetHandle();
}

bool FCoPresenceFabModule::CanSavePackage(UPackage* Package, const FString& Filename, FOutputDevice* Error)
{
	// Multi-User sends every save of a package to its file in full to everyone in the
	// session and keeps each one, so agents only save content they share on purpose
	// (SharePackages). Copies written anywhere else go through: autosaves, and the copy
	// Multi-User sends of an asset just created.
	FString PackageFilename;
	if (!bPersisting && Package && Package->GetFName() != SharingPackage
		&& FPackageName::TryConvertLongPackageNameToFilename(Package->GetName(), PackageFilename, FPaths::GetExtension(Filename, true))
		&& FPaths::IsSamePath(PackageFilename, Filename))
	{
		RefusedSaves.AddUnique(Package->GetName());
		UE_LOG(LogCoPresenceFab, Warning, TEXT("Agents' editors don't save, so %s wasn't saved"), *Package->GetName());

		// Multi-User locks a package for whoever changes it until they save it, so people
		// couldn't save this one. The lock goes on the next tick, once the edit that tried
		// to save has been undone, since the undo changes the package again.
		LocksToRelease.AddUnique(Package->GetFName());
		if (!ReleaseLocksHandle.IsValid())
		{
			ReleaseLocksHandle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateRaw(this, &FCoPresenceFabModule::ReleaseRefusedLocks));
		}
		return false;
	}
	return !PreviousSaveCheck.IsBound() || PreviousSaveCheck.Execute(Package, Filename, Error);
}

bool FCoPresenceFabModule::ReleaseRefusedLocks(float DeltaTime)
{
	ReleaseLocksHandle.Reset();
	TArray<FName> Packages = MoveTemp(LocksToRelease);
	const TSharedPtr<IConcertSyncClient> Client = UE::CoPresenceFab::Private::GetMultiUserClient();
	const TSharedPtr<IConcertClientWorkspace> Workspace = Client.IsValid() ? Client->GetWorkspace() : nullptr;
	if (Workspace.IsValid())
	{
		// Multi-User only unlocks the ones this editor holds
		Workspace->UnlockResources(MoveTemp(Packages));
	}
	return false;
}

void FCoPresenceFabModule::StartupModule()
{
	if (FParse::Param(FCommandLine::Get(), UE::CoPresenceFab::Private::AgentEditorSwitch))
	{
		// The editor sets up its own save check while it starts, after this module has loaded
		FCoreDelegates::GetOnPostEngineInit().AddRaw(this, &FCoPresenceFabModule::RefuseSaves);
	}
}

void FCoPresenceFabModule::ShutdownModule()
{
	FCoreDelegates::GetOnPostEngineInit().RemoveAll(this);
	// Only while the check in place is this module's; Multi-User puts back its own copy when it leaves
	if (SaveCheckHandle.IsValid() && FCoreUObjectDelegates::IsPackageOKToSaveDelegate.GetHandle() == SaveCheckHandle)
	{
		FCoreUObjectDelegates::IsPackageOKToSaveDelegate = PreviousSaveCheck;
	}
	SaveCheckHandle.Reset();
	PreviousSaveCheck.Unbind();
	RefusedSaves.Reset();
	FTSTicker::GetCoreTicker().RemoveTicker(ReleaseLocksHandle);
	ReleaseLocksHandle.Reset();
	LocksToRelease.Reset();

	HeldBackFolders.Reset();
	HeldBackFiles.Reset();
	UpdatePackageFilter();
	Channel.Reset();
	ConnectedBrowser.Reset();
	Dialogs.Reset();
}

IMPLEMENT_MODULE(FCoPresenceFabModule, CoPresenceFab)


bool FCoPresenceFabModule::RenameCurrentMapForCheckpoint(const FString& TargetPackage)
{
	UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
	if (!World || !World->GetOutermost()->GetName().StartsWith(TEXT("/Game/Maps/"))
		|| !TargetPackage.StartsWith(TEXT("/Game/Maps/"))
		|| !FPackageName::IsValidLongPackageName(TargetPackage)
		|| FPackageName::DoesPackageExist(TargetPackage)
		|| FindPackage(nullptr, *TargetPackage))
	{
		UE_LOG(LogCoPresenceFab, Error, TEXT("Map rename refused: invalid source or existing destination"));
		return false;
	}
	const TArray<FAssetRenameData> Renames = {
		FAssetRenameData(World, FPackageName::GetLongPackagePath(TargetPackage), FPackageName::GetLongPackageAssetName(TargetPackage))
	};
	TGuardValue<bool> Persisting(bPersisting, true);
	return FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools")).Get().RenameAssets(Renames);
}
