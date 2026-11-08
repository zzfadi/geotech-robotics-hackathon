#pragma once

namespace HumanTouchPassthrough
{
    HUMANTOUCHXR_API bool IsRequested();
    HUMANTOUCHXR_API bool IsActive();
    HUMANTOUCHXR_API bool IsSupported();
    HUMANTOUCHXR_API void Request(bool bEnable);
}
