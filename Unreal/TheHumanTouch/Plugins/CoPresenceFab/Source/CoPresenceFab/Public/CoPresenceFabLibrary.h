#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "Styling/SlateTypes.h"

#include "CoPresenceFabLibrary.generated.h"

/** Carries what a script in the Fab tab's page reports back to the editor. */
UCLASS(BlueprintType, NotBlueprintable, Transient)
class COPRESENCEFAB_API UCoPresenceFabChannel : public UObject
{
	GENERATED_BODY()

public:
	DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnPageReport, const FString&, Json);

	/** What the page reported, as JSON. */
	UPROPERTY(BlueprintAssignable, Category = "CoPresence|Fab")
	FOnPageReport OnReport;

	/** Called by the page as window.ue.copresence.report(json). */
	UFUNCTION()
	void Report(const FString& Json);
};

/** What happened to one package SharePackages was given. */
UENUM(BlueprintType)
enum class ECoPresenceShareResult : uint8
{
	/** The session got this version of the package */
	Shared,
	/** It hasn't changed since it was last saved, so there was nothing to send */
	Unchanged,
	/** It wasn't sent; the result's Problem says why */
	Refused,
};

USTRUCT(BlueprintType)
struct COPRESENCEFAB_API FCoPresenceSharedPackage
{
	GENERATED_BODY()

	UPROPERTY(BlueprintReadOnly, Category = "CoPresence")
	FString PackageName;

	UPROPERTY(BlueprintReadOnly, Category = "CoPresence")
	ECoPresenceShareResult Result = ECoPresenceShareResult::Refused;

	/** Size of the copy the session got, in bytes; 0 unless it was shared */
	UPROPERTY(BlueprintReadOnly, Category = "CoPresence")
	int64 SizeBytes = 0;

	/** Why it wasn't shared, in a sentence an agent can pass on */
	UPROPERTY(BlueprintReadOnly, Category = "CoPresence")
	FString Problem;
};

class UWidget;

/**
 * Drives the Fab plugin's own tab, so a listing a person approved is added through the
 * plugin's Add to Project exactly as if they had clicked it, shows the requests to
 * approve in windows of their own, and keeps imports out of the Multi-User session until
 * they are saved. Agents' editors refuse to save assets and levels, except the content
 * they share with SharePackages.
 */
UCLASS()
class COPRESENCEFAB_API UCoPresenceFabLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Runs a script in the page of the Fab plugin's tab.
	 * @return False when no Fab tab is open.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Fab")
	static bool RunScriptInFabTab(const FString& Script);

	/** The address the Fab plugin's tab shows, or empty when no Fab tab is open. */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Fab")
	static FString GetFabTabUrl();

	/**
	 * Lets the Fab tab's page report back by calling window.ue.copresence.report(json).
	 * The first time for a tab, its page reloads so it can see that object.
	 * @return The channel the reports arrive on, or null when no Fab tab is open.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Fab")
	static UCoPresenceFabChannel* ConnectFabTab();

	/**
	 * Shows a widget in a window of its own, in front of the editor and sized to fit the
	 * widget. Build the widget first, so the window opens at its size.
	 * @return False when the editor has no windows to show it in.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Fab")
	static bool OpenDialog(UWidget* Content, const FText& Title);

	/** Closes the window OpenDialog showed a widget in. */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Fab")
	static void CloseDialog(UWidget* Content);

	/** Whether the window OpenDialog showed a widget in is still open; people can close it. */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Fab")
	static bool IsDialogOpen(UWidget* Content);

	/** The editor's own check box look. UMG's default is a light gray box that stands out in editor windows. */
	UFUNCTION(BlueprintPure, Category = "CoPresence|Fab")
	static FCheckBoxStyle GetEditorCheckBoxStyle();

	/**
	 * Keeps assets created in a folder, and below it, out of the Multi-User session until
	 * they are saved. Multi-User otherwise sends a copy of each asset the moment it is
	 * created, and a Fab import announces each asset twice before it is saved.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Fab")
	static void HoldBackNewAssets(const FString& Folder, bool bHoldBack);

	/**
	 * Multi-User writes each asset it holds back into the project's own folder, outside the
	 * session's sandbox. Once the assets in a folder are saved, which shares them with the
	 * session, this deletes those files, so the project folder stays as it was.
	 * @return How many files were deleted.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence|Fab")
	static int32 DeleteHeldBackFiles(const FString& Folder);

	/**
	 * Packages this editor refused to save since the last call. Agents' editors, started
	 * with -COPRESENCEAGENT, refuse to save packages to their files outside
	 * PersistSessionChanges: Multi-User sends each save in full to everyone in the session
	 * and keeps it, so saving is left to people.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence")
	static TArray<FString> TakeRefusedSaves();

	/**
	 * Writes the Multi-User session's changes into the project's files, as Multi-User's own
	 * persist does: it saves live transaction packages, then copies sandbox files in.
	 * Use PersistContentPackages for edited materials/textures without live transactions.
	 * These are the only saves an agent's editor makes; Tools/copresence.sh save persists
	 * through it.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence")
	static void PersistSessionChanges();

	/** Explicitly save and persist selected loaded /Game content packages. Requires a live
	 * Multi-User workspace and human authorization. Materials/textures lack live transaction
	 * support, so ordinary session persistence can otherwise copy their initial empty asset.
	 * Does not save maps or unrelated dirty packages. */
	UFUNCTION(BlueprintCallable, Category = "CoPresence")
	static bool PersistContentPackages(const TArray<FString>& PackageNames);
	/** Explicit host save for selected loaded /Game packages in a solo editor only.
	 * Refuses any active Multi-User workspace; ordinary agent save guard stays enabled. */
	UFUNCTION(BlueprintCallable, Category = "CoPresence")
	static bool PersistSoloPackages(const TArray<FString>& PackageNames);

	/**
	 * Shares changed content packages with everyone in the Multi-User session by saving them,
	 * which is how Multi-User sends assets whose edits it doesn't carry live, such as
	 * materials and textures. Saves go to the session only, never to the project's files.
	 * Never saves levels or the packages of actors in them, packages someone else has locked
	 * or has unsaved changes in, or packages bigger than GetMaxSharedPackageBytes: everyone
	 * in the session receives each save in full and the server keeps every one. Agents'
	 * editors can save only through this and the persist functions.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence")
	static TArray<FCoPresenceSharedPackage> SharePackages(const TArray<FString>& PackageNames);

	/** The biggest package SharePackages sends, in bytes. */
	UFUNCTION(BlueprintPure, Category = "CoPresence")
	static int64 GetMaxSharedPackageBytes();

	/**
	 * Quits the editor the way the engine quits when the system asks it to, without asking
	 * about unsaved changes: closing the editor's window asks, and a headless editor can't
	 * answer. Nothing is saved, so an agent shares its content before it calls this.
	 */
	UFUNCTION(BlueprintCallable, Category = "CoPresence")
	static void QuitWithoutSaving();
};
