#pragma once

#include "EditorUtilityWidget.h"
#include "HumanTouchPerformerWidget.generated.h"

/** Dockable local editor controls for the three Live Link performers. */
UCLASS()
class UHumanTouchPerformerWidget : public UEditorUtilityWidget
{
    GENERATED_BODY()
  protected:
    virtual TSharedRef<SWidget> RebuildWidget() override;
};
