#include "HumanTouchPerformerWidget.h"
#include "HumanTouchPassthrough.h"
#include "HumanTouchXRTable.h"
#include "Widgets/Input/SButton.h"
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
    Box->AddSlot().AutoHeight().HAlign(HAlign_Left).Padding(10, 8)
        [SNew(SCheckBox)
             .IsChecked_Lambda([] { return HumanTouchPassthrough::IsRequested() ? ECheckBoxState::Checked : ECheckBoxState::Unchecked; })
             .OnCheckStateChanged_Lambda([](ECheckBoxState State) { HumanTouchPassthrough::Request(State == ECheckBoxState::Checked); })
             [SNew(STextBlock).Text(NSLOCTEXT("HumanTouch", "RoomPassthrough", "Show room behind scene objects"))]];
    Box->AddSlot().AutoHeight().Padding(10, 0, 10, 5)
        [SNew(STextBlock).AutoWrapText(true).Text_Lambda([]
        {
            if (HumanTouchPassthrough::IsRequested() && !HumanTouchPassthrough::IsActive())
                return NSLOCTEXT("HumanTouch", "WaitingForAlpha", "Waiting for an XR runtime with alpha-blend support. On the Quest Mac, launch with --passthrough.");
            return FText::GetEmpty();
        })];
    Box->AddSlot().AutoHeight().Padding(10, 4)
        [SNew(SCheckBox)
            .IsChecked_Lambda([] { return HumanTouchXRTable::IsHidingOthers() ? ECheckBoxState::Checked : ECheckBoxState::Unchecked; })
            .OnCheckStateChanged_Lambda([](ECheckBoxState State) { HumanTouchXRTable::HideOthers(State == ECheckBoxState::Checked); })
            [SNew(STextBlock).Text(NSLOCTEXT("HumanTouch", "HideOtherTables", "Hide other tables in XR"))]];
    Box->AddSlot().AutoHeight().Padding(10, 4)
        [SNew(STextBlock).AutoWrapText(true).Text(NSLOCTEXT("HumanTouch", "TablePose", "Place your headset above the middle of the near long edge and look straight across."))];
    Box->AddSlot().AutoHeight().HAlign(HAlign_Left).Padding(10, 4)
        [SNew(SButton)
            .Text_Lambda([] { return FText::Format(NSLOCTEXT("HumanTouch", "AlignXRTable", "Align table {0} in 5 seconds"), HumanTouchXRTable::Number()); })
            .OnClicked_Lambda([] { HumanTouchXRTable::AlignAfter(5); return FReply::Handled(); })];
    Box->AddSlot().AutoHeight().Padding(10, 4)
        [SNew(STextBlock).AutoWrapText(true).Text_Lambda([] { return FText::FromString(HumanTouchXRTable::Status()); })];
    Box->AddSlot().AutoHeight().HAlign(HAlign_Left).Padding(10, 4)
        [SNew(SButton)
            .Text_Lambda([] { return HumanTouchXRTable::SurveyRunning()
                ? NSLOCTEXT("HumanTouch", "NextSurveyMark", "Capture marked point")
                : NSLOCTEXT("HumanTouch", "BeginSurvey", "Survey with right-controller probe"); })
            .OnClicked_Lambda([] {
                if (HumanTouchXRTable::SurveyRunning()) HumanTouchXRTable::CaptureMark();
                else HumanTouchXRTable::BeginSurvey();
                return FReply::Handled();
            })];
    Box->AddSlot().AutoHeight().HAlign(HAlign_Left).Padding(10, 0)
        [SNew(SButton).Text(NSLOCTEXT("HumanTouch", "CancelSurvey", "Cancel survey"))
            .Visibility_Lambda([] { return HumanTouchXRTable::SurveyRunning() ? EVisibility::Visible : EVisibility::Collapsed; })
            .OnClicked_Lambda([] { HumanTouchXRTable::CancelSurvey(); return FReply::Handled(); })];
    Box->AddSlot().AutoHeight().HAlign(HAlign_Left).Padding(10, 2)
        [SNew(SButton).Text(NSLOCTEXT("HumanTouch", "SurveyHand", "Survey with tracked fingertip"))
            .Visibility_Lambda([] { return HumanTouchXRTable::SurveyRunning() ? EVisibility::Collapsed : EVisibility::Visible; })
            .OnClicked_Lambda([] { HumanTouchXRTable::BeginSurvey(false); return FReply::Handled(); })];
    Box->AddSlot().AutoHeight().HAlign(HAlign_Left).Padding(10, 2)
        [SNew(SButton).Text(NSLOCTEXT("HumanTouch", "CheckMounts", "Recheck both mounts"))
            .Visibility_Lambda([] { return HumanTouchXRTable::CanCheckSurvey() ? EVisibility::Visible : EVisibility::Collapsed; })
            .OnClicked_Lambda([] { HumanTouchXRTable::CheckSurvey(); return FReply::Handled(); })];
    return Box;
}
