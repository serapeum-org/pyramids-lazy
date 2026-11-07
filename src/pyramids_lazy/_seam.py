"""Import-time check that core's ``pyramids.lazy_support`` facade can satisfy this backend.

Core's facade assigns this obligation to the backend: "A lazy backend pins
``pyramids.lazy_support.LAZY_SUPPORT_API_VERSION >= N`` at its own import and fails loudly
('upgrade pyramids-core') rather than on a later missing attribute." Without the check, a
pyramids-core that predates the facade — every release up to and including 0.74.0 — surfaces as a
raw ``ModuleNotFoundError`` from whichever submodule imported first, which names neither the real
cause nor the fix. That is also why the published ``pyramids-core`` floor cannot carry this on its
own: a version specifier cannot say "must publish the lazy_support facade".
"""

from __future__ import annotations

#: The facade contract this backend is written against. Core bumps its own
#: ``LAZY_SUPPORT_API_VERSION`` only on a breaking change, so ``>=`` is the documented test.
REQUIRED_LAZY_SUPPORT_API_VERSION = 1

_FACADE_MISSING = (
    "pyramids-lazy requires a pyramids-core that publishes the `pyramids.lazy_support` facade "
    f"(API version >= {REQUIRED_LAZY_SUPPORT_API_VERSION}), but the installed pyramids-core has no "
    "`pyramids.lazy_support` module. Upgrade pyramids-core."
)
_FACADE_TOO_OLD = (
    "pyramids-lazy requires `pyramids.lazy_support` API version >= {required}, but the installed "
    "pyramids-core publishes version {found}. Upgrade pyramids-core."
)


def require_core_seam() -> None:
    """Raise ``ImportError`` unless core's facade satisfies this backend's contract.

    Raises:
        ImportError: When ``pyramids.lazy_support`` is absent, or publishes an API version older
            than :data:`REQUIRED_LAZY_SUPPORT_API_VERSION`. The import sits inside the guard
            because detecting its absence is the whole point.
    """
    try:
        from pyramids.lazy_support import LAZY_SUPPORT_API_VERSION
    except ImportError as exc:
        raise ImportError(_FACADE_MISSING) from exc
    if LAZY_SUPPORT_API_VERSION < REQUIRED_LAZY_SUPPORT_API_VERSION:
        raise ImportError(
            _FACADE_TOO_OLD.format(
                required=REQUIRED_LAZY_SUPPORT_API_VERSION,
                found=LAZY_SUPPORT_API_VERSION,
            )
        )


# Importing this module IS the check: it runs here, at module scope, so that a plain
# `import pyramids_lazy._seam` performs it without the caller having to remember a call.
require_core_seam()
