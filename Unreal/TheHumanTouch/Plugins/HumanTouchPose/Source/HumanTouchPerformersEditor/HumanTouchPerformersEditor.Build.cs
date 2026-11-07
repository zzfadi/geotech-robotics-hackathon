using UnrealBuildTool;

public class HumanTouchPerformersEditor : ModuleRules
{
    public HumanTouchPerformersEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PrivateDependencyModuleNames.AddRange(new[] {
            "Core", "CoreUObject", "Engine", "UnrealEd", "AnimGraph", "AnimGraphRuntime", "HumanTouchPose", "LiveLinkAnimationCore",
            "Blutility", "UMG", "Slate", "SlateCore", "ToolMenus", "LevelEditor",
            "IKRig", "IKRigEditor", "AnimationCore", "PerformanceCaptureCore", "DataflowEngine", "ChaosOutfitAssetEngine", "ChaosClothAssetEngine", "HairStrandsCore",
            "MetaHumanCharacter", "MetaHumanCharacterEditor", "MetaHumanCharacterPaletteEditor"
        });
    }
}
