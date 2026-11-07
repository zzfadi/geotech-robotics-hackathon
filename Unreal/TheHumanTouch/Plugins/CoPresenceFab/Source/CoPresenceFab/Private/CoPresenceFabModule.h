#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "Modules/ModuleInterface.h"
#include "UObject/StrongObjectPtr.h"
#include "UObject/UObjectGlobals.h"

class IConcertClientWorkspace;
class SWebBrowser;
class SWindow;
class UCoPresenceFabChannel;
class UPackage;
class UWidget;
enum class EPackageFilterResult : uint8;
struct FConcertPackageInfo;
struct FCoPresenceSharedPackage;

class FCoPresenceFabModule : public IModuleInterface
{
public:
	/** Multi-User sends each shared package to everyone in the session and keeps every save,
	 * so bigger content goes through git; Fab imports in people's editors stop at the same size */
	static constexpr int64 MaxSharedPackageBytes = 40 * 1024 * 1024;

	static FCoPresenceFabModule& Get();

	/** The channel the Fab tab's page reports on, bound into that page if it isn't yet. */
	UCoPresenceFabChannel* ConnectBrowser(const TSharedRef<SWebBrowser>& Browser);

	/** Shows a widget in a window of its own, sized to fit it, over the editor's main window. */
	bool OpenDialog(UWidget* Content, const FText& Title);
	void CloseDialog(const UWidget* Content);
	bool IsDialogOpen(const UWidget* Content) const;

	/** Keeps assets created in a folder out of the Multi-User session until they are saved, or stops doing that. */
	void HoldBackNewAssets(const FString& Folder, bool bHoldBack);

	/** Deletes the project folder copies Multi-User wrote of held back assets in a folder that have been saved since. */
	int32 DeleteHeldBackFiles(const FString& Folder);

	/** Packages this editor refused to save since the last call, which forgets them. */
	TArray<FString> TakeRefusedSaves();

	/** Multi-User's persist, with the saves it makes allowed in an agent's editor too. */
	void PersistSessionChanges(bool bPublishMaps = false);
	bool RenameCurrentMapForCheckpoint(const FString& TargetPackage);
	bool PersistContentPackages(const TArray<FString>& PackageNames);
	bool PersistSoloPackages(const TArray<FString>& PackageNames);

	/** Saves changed content packages into the Multi-User session; see UCoPresenceFabLibrary::SharePackages. */
	TArray<FCoPresenceSharedPackage> SharePackages(const TArray<FString>& PackageNames);

	//~ Begin IModuleInterface
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
	//~ End IModuleInterface

private:
	struct FDialog
	{
		/** Kept alive while its window shows it */
		TStrongObjectPtr<UWidget> Content;
		TWeakPtr<SWindow> Window;
	};

	void HandleDialogClosed(const TSharedRef<SWindow>& Window);

	/** Multi-User asks this about each package it is about to send to the session. */
	EPackageFilterResult FilterPackage(const FConcertPackageInfo& PackageInfo);

	/** Registers the package filter with Multi-User while any folder is held back. */
	void UpdatePackageFilter();

	/** Puts the save check of agents' editors in front of the one the editor has. */
	void RefuseSaves();

	/** Asked before each package save in an agent's editor; see RefuseSaves. */
	bool CanSavePackage(UPackage* Package, const FString& Filename, FOutputDevice* Error);

	/** Gives up the Multi-User locks on packages this editor was refused to save. */
	bool ReleaseRefusedLocks(float DeltaTime);

	/** Why a package can't be shared with the session, or empty when it can. */
	FString GetShareProblem(const FString& PackageName, UPackage* Package, IConcertClientWorkspace& Workspace) const;

	TStrongObjectPtr<UCoPresenceFabChannel> Channel;

	/** The browser the channel is bound into; a new Fab tab needs binding again. */
	TWeakPtr<SWebBrowser> ConnectedBrowser;

	TArray<FDialog> Dialogs;

	/** Folders whose new assets wait for their first save, each ending in a slash. */
	TArray<FString> HeldBackFolders;

	/** Packages held back so far, with the file Multi-User wrote for each in the project folder */
	TMap<FName, FString> HeldBackFiles;

	bool bPackageFilterRegistered = false;

	/** The save check that was in place before this module's, which gets the saves this one allows */
	FCoreUObjectDelegates::FIsPackageOKToSaveDelegate PreviousSaveCheck;

	/** Tells this module's save check apart from the ones put in front of it */
	FDelegateHandle SaveCheckHandle;

	/** Packages refused since TakeRefusedSaves last ran */
	TArray<FString> RefusedSaves;

	/** Refused packages whose locks go on the next tick */
	TArray<FName> LocksToRelease;
	FTSTicker::FDelegateHandle ReleaseLocksHandle;

	/** Set while PersistSessionChanges runs */
	bool bPersisting = false;

	/** The package SharePackages is saving, which the save check lets through */
	FName SharingPackage;
};
