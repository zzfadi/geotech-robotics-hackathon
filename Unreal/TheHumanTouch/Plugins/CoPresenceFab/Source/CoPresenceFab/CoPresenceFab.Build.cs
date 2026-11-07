using UnrealBuildTool;

public class CoPresenceFab : ModuleRules
{
	public CoPresenceFab(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.NoPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"SlateCore",
			"UMG",
			"WebBrowserWidget",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"ConcertSyncClient",
			"ConcertSyncCore",
			"MultiUserClient",
			"UnrealEd",
			"AssetTools",
			"Slate",
			"WebBrowser",
		});
	}
}
