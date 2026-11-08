using UnrealBuildTool;

public class HumanTouchXR : ModuleRules
{
    public HumanTouchXR(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.Add("Core");
        PrivateDependencyModuleNames.AddRange(new[] {
            "CoreUObject", "Engine", "UnrealEd", "Projects", "OpenXRHMD", "OpenXR",
            "HeadMountedDisplay", "Renderer", "RenderCore", "RHI", "Json"
        });
    }
}
