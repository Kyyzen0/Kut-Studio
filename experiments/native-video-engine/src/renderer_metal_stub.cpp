#include "kut_engine/renderer.hpp"

// Metal backend is only built on Apple (see renderer_metal.mm). This translation
// unit keeps the symbol available on other platforms.

namespace kut {

#if !defined(KUT_ENGINE_HAS_METAL)
// create_metal_renderer() is already defined in renderer.cpp to return nullptr.
#endif

}  // namespace kut
