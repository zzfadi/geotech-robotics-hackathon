using UnrealBuildTool;

public class HubPoseExport : ModuleRules
{
    public HubPoseExport(ReadOnlyTargetRules Target) : base(Target)
    {
        PrivateDependencyModuleNames.AddRange(new[] {
            "Core", "CoreUObject", "Engine", "LiveLinkInterface", "Sockets", "Networking", "Json"
        });
    }
}
