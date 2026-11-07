using UnrealBuildTool;

public class HumanTouchPerformersEditor : ModuleRules
{
    public HumanTouchPerformersEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PrivateDependencyModuleNames.AddRange(new[] {
            "Core", "CoreUObject", "Engine", "UnrealEd", "AnimGraph", "AnimGraphRuntime", "HumanTouchPose", "LiveLinkAnimationCore",
            "MetaHumanCharacter", "MetaHumanCharacterEditor", "MetaHumanCharacterPaletteEditor"
        });
    }
}
