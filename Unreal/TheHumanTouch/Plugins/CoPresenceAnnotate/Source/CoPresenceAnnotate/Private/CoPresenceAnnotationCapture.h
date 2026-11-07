#pragma once

#include "CoreMinimal.h"

class SWindow;

namespace UE::CoPresenceAnnotate
{
	/**
	 * Pictures a window as it draws now, as JPEG: all of it, and when the drawing covers only
	 * part of it, a closer picture of that part.
	 *
	 * @param DrawingBounds Where the drawing is, in desktop space.
	 * @param OutDetail Left empty when the drawing covers most of the window.
	 * @return False when the window couldn't be read back.
	 */
	bool CaptureWindow(const TSharedRef<SWindow>& Window, const FBox2D& DrawingBounds, TArray64<uint8>& OutFull, TArray64<uint8>& OutDetail);
}
