#include "CoPresenceFabBrowser.h"

#include "Async/Async.h"
#include "IWebBrowserWindow.h"
#include "SWebBrowser.h"
#include "WebBrowserModule.h"

namespace UE::CoPresenceFab::Private
{
	// Stands in for the project name in the user agent
	const TCHAR* const UserAgentApplication = TEXT("CoPresence");
}

TSharedRef<SWidget> UCoPresenceFabBrowser::RebuildWidget()
{
	if (IsDesignTime())
	{
		return Super::RebuildWidget();
	}

	WebBrowserWidget = SNew(SWebBrowser)
		.InitialURL(InitialURL)
		.ShowControls(false)
		.SupportsTransparency(bSupportsTransparency)
		.UserAgentApplication(IWebBrowserModule::MakeUserAgentApplication(UE::CoPresenceFab::Private::UserAgentApplication))
		.OnUrlChanged(BIND_UOBJECT_DELEGATE(FOnTextChanged, HandleOnUrlChanged))
		.OnBeforePopup(BIND_UOBJECT_DELEGATE(FOnBeforePopupDelegate, HandleOnBeforePopup))
		.OnConsoleMessage(BIND_UOBJECT_DELEGATE(FOnConsoleMessageDelegate, HandleOnConsoleMessage))
		.OnLoadCompleted(BIND_UOBJECT_DELEGATE(FSimpleDelegate, HandleOnLoadCompleted));

	return WebBrowserWidget.ToSharedRef();
}

void UCoPresenceFabBrowser::HandleOnLoadCompleted()
{
	if (IsInGameThread())
	{
		OnLoadCompleted.Broadcast();
		return;
	}

	// macOS reports loads on its main thread, which isn't the game thread Python listens on
	AsyncTask(ENamedThreads::GameThread, [WeakBrowser = TWeakObjectPtr<UCoPresenceFabBrowser>(this)]()
	{
		if (UCoPresenceFabBrowser* Browser = WeakBrowser.Get())
		{
			Browser->OnLoadCompleted.Broadcast();
		}
	});
}
