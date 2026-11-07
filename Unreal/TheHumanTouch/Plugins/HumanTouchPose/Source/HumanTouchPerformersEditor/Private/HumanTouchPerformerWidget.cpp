#include "HumanTouchPerformerWidget.h"
#include "HumanTouchPerformerMode.h"
#include "Styling/AppStyle.h"
#include "Widgets/Input/SCheckBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

TSharedRef<SWidget> UHumanTouchPerformerWidget::RebuildWidget()
{
    TSharedRef<SVerticalBox> Box = SNew(SVerticalBox);
    auto AddChoice = [&Box](EHumanTouchCast Mode, FText Title)
    {
        Box->AddSlot()
            .AutoHeight()
            .HAlign(HAlign_Left)
            .Padding(10, 5)[SNew(SCheckBox)
                                .Style(&FAppStyle::Get().GetWidgetStyle<FCheckBoxStyle>("RadioButton"))
                                .IsChecked_Lambda(
                                    [Mode]
                                    {
                                        return GetHumanTouchCast() == Mode ? ECheckBoxState::Checked
                                                                           : ECheckBoxState::Unchecked;
                                    })
                                .OnCheckStateChanged_Lambda(
                                    [Mode](ECheckBoxState State)
                                    {
                                        if (State == ECheckBoxState::Checked)
                                            SetHumanTouchCast(Mode);
                                    })[SNew(STextBlock).Text(Title)]];
    };
    AddChoice(EHumanTouchCast::Mannequins,
              NSLOCTEXT("HumanTouch", "Mannequins", "Mannequins (Manny and Quinn)"));
    AddChoice(EHumanTouchCast::MetaHumans,
              NSLOCTEXT("HumanTouch", "MetaHumans", "MetaHumans (Victor, Bruce and Ada)"));
    return Box;
}
