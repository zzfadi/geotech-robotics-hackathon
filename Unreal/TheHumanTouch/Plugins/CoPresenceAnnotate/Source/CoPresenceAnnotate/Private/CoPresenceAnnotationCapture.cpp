#include "CoPresenceAnnotationCapture.h"

#include "Framework/Application/SlateApplication.h"
#include "ImageCore.h"
#include "ImageUtils.h"
#include "Widgets/SWindow.h"

namespace UE::CoPresenceAnnotate::Private
{
	// Agents look at pictures at most about this big, so larger ones only cost upload time
	constexpr int32 FullMaxEdge = 1568;
	constexpr int32 DetailMaxEdge = 1200;
	constexpr int32 JpegQuality = 82;

	// The close-up shows this much around the drawing, as a share of its size on each side,
	// and at least this many pixels across
	constexpr double DetailMarginShare = 0.5;
	constexpr int32 DetailMinimumPixels = 640;

	// A drawing over more than this share of the window gets no close-up
	constexpr double DetailMaxShare = 0.45;

	TArray64<uint8> Encode(TArray<FColor> Pixels, int32 Width, int32 Height, int32 MaxEdge)
	{
		const double Scale = FMath::Min(1.0, static_cast<double>(MaxEdge) / FMath::Max(Width, Height));
		if (Scale < 1.0)
		{
			const int32 ScaledWidth = FMath::Max(1, FMath::RoundToInt32(Width * Scale));
			const int32 ScaledHeight = FMath::Max(1, FMath::RoundToInt32(Height * Scale));
			TArray<FColor> Scaled;
			Scaled.SetNumUninitialized(ScaledWidth * ScaledHeight);
			FImageUtils::ImageResize(Width, Height, Pixels, ScaledWidth, ScaledHeight, Scaled, false, true);
			Pixels = MoveTemp(Scaled);
			Width = ScaledWidth;
			Height = ScaledHeight;
		}

		TArray64<uint8> Jpeg;
		FImageUtils::CompressImage(Jpeg, TEXT("jpg"), FImageView(Pixels.GetData(), Width, Height), JpegQuality);
		return Jpeg;
	}

	TArray<FColor> Crop(const TArray<FColor>& Pixels, int32 Width, const FIntRect& Rect)
	{
		TArray<FColor> Cropped;
		Cropped.Reserve(Rect.Area());
		for (int32 Y = Rect.Min.Y; Y < Rect.Max.Y; ++Y)
		{
			Cropped.Append(&Pixels[Y * Width + Rect.Min.X], Rect.Width());
		}
		return Cropped;
	}
}

bool UE::CoPresenceAnnotate::CaptureWindow(const TSharedRef<SWindow>& Window, const FBox2D& DrawingBounds, TArray64<uint8>& OutFull, TArray64<uint8>& OutDetail)
{
	using namespace UE::CoPresenceAnnotate::Private;

	TArray<FColor> Pixels;
	FIntVector Size;
	if (!FSlateApplication::Get().TakeScreenshot(Window, Pixels, Size) || Size.X <= 0 || Size.Y <= 0)
	{
		return false;
	}
	for (FColor& Pixel : Pixels)
	{
		Pixel.A = 255;
	}

	OutFull = Encode(Pixels, Size.X, Size.Y, FullMaxEdge);
	OutDetail.Reset();
	if (!DrawingBounds.bIsValid)
	{
		return true;
	}

	// From desktop space to the picture's pixels, which can differ on high density screens
	const FVector2D WindowPosition(Window->GetPositionInScreen());
	const FVector2D PixelsPerUnit = FVector2D(Size.X, Size.Y) / FVector2D(Window->GetSizeInScreen());
	FBox2D Region((DrawingBounds.Min - WindowPosition) * PixelsPerUnit, (DrawingBounds.Max - WindowPosition) * PixelsPerUnit);
	Region = Region.ExpandBy(Region.GetSize() * DetailMarginShare);
	const FVector2D Shortfall = FVector2D(DetailMinimumPixels) - Region.GetSize();
	Region = Region.ExpandBy(FVector2D(FMath::Max(0.0, Shortfall.X), FMath::Max(0.0, Shortfall.Y)) * 0.5);

	FIntRect Rect(FMath::FloorToInt32(Region.Min.X), FMath::FloorToInt32(Region.Min.Y), FMath::CeilToInt32(Region.Max.X), FMath::CeilToInt32(Region.Max.Y));
	Rect.Clip(FIntRect(0, 0, Size.X, Size.Y));
	if (Rect.Area() <= 0 || Rect.Area() > DetailMaxShare * Size.X * Size.Y)
	{
		return true;
	}

	OutDetail = Encode(Crop(Pixels, Size.X, Rect), Rect.Width(), Rect.Height(), DetailMaxEdge);
	return true;
}
