using UnrealBuildTool;

public class HumanTouchPose : ModuleRules
{
    public HumanTouchPose(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.NoPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "LiveLinkInterface" });
        PrivateDependencyModuleNames.AddRange(new[] { "Json", "Networking", "Sockets" });
    }
}
