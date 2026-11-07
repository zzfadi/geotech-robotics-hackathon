#pragma once
#include "LiveLinkSourceFactory.h"
#include "HumanTouchPoseSourceFactory.generated.h"

UCLASS()
class HUMANTOUCHPOSE_API UHumanTouchPoseSourceFactory : public ULiveLinkSourceFactory
{
    GENERATED_BODY()
public:
    virtual FText GetSourceDisplayName() const override;
    virtual FText GetSourceTooltip() const override;
    virtual EMenuType GetMenuType() const override { return EMenuType::MenuEntry; }
    virtual TSharedPtr<ILiveLinkSource> CreateSource(const FString& ConnectionString) const override;
};
