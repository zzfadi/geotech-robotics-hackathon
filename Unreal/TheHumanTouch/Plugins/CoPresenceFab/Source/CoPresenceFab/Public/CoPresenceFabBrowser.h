#pragma once

#include "CoreMinimal.h"
#include "WebBrowser.h"

#include "CoPresenceFabBrowser.generated.h"

/**
 * The UMG web browser with a user agent fab.com accepts. The default user agent names the
 * project, and Cloudflare's check on fab.com fails when that name contains "agent", as
 * the Fab plugin notes for its own browser. Also says when a page has finished loading.
 */
UCLASS()
class COPRESENCEFAB_API UCoPresenceFabBrowser : public UWebBrowser
{
	GENERATED_BODY()

public:
	DECLARE_DYNAMIC_MULTICAST_DELEGATE(FOnLoadCompleted);

	/** Called when a page has finished loading. */
	UPROPERTY(BlueprintAssignable, Category = "Web Browser|Event")
	FOnLoadCompleted OnLoadCompleted;

protected:
	//~ Begin UWidget Interface
	virtual TSharedRef<SWidget> RebuildWidget() override;
	//~ End UWidget Interface

private:
	void HandleOnLoadCompleted();
};
