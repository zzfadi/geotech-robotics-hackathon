#include "CoPresenceAnnotationLibrary.h"

#include "CoPresenceAnnotateModule.h"

void UCoPresenceAnnotationLibrary::SetAnnotating(bool bAnnotating)
{
	if (FCoPresenceAnnotateModule::IsAvailable())
	{
		FCoPresenceAnnotateModule::Get().SetAnnotating(bAnnotating);
	}
}

bool UCoPresenceAnnotationLibrary::IsAnnotating()
{
	return FCoPresenceAnnotateModule::IsAvailable() && FCoPresenceAnnotateModule::Get().IsAnnotating();
}

bool UCoPresenceAnnotationLibrary::HasAnnotation()
{
	return FCoPresenceAnnotateModule::IsAvailable() && FCoPresenceAnnotateModule::Get().HasAnnotation();
}

FString UCoPresenceAnnotationLibrary::TakeAnnotation()
{
	return FCoPresenceAnnotateModule::IsAvailable() ? FCoPresenceAnnotateModule::Get().TakeAnnotation() : FString();
}

TArray<FCoPresenceAnnotationNote> UCoPresenceAnnotationLibrary::TakeNotes()
{
	return FCoPresenceAnnotateModule::IsAvailable() ? FCoPresenceAnnotateModule::Get().TakeNotes() : TArray<FCoPresenceAnnotationNote>();
}

void UCoPresenceAnnotationLibrary::SetNoteHint(const FText& Hint)
{
	if (FCoPresenceAnnotateModule::IsAvailable())
	{
		FCoPresenceAnnotateModule::Get().SetNoteHint(Hint);
	}
}
