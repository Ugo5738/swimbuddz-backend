"""Shared chat schema constants and helpers.

Allowed reactions and body length cap come from design doc §8.1, §8.3.
"""

# Hard cap on message body length. See design doc §8.1 — anything bigger is a
# document, not a chat message. Stored as TEXT in the DB so this can move
# without a schema change.
MAX_MESSAGE_BODY_LENGTH = 4000

# Restricted starter set. See design doc §8.3 — admin-configurable later.
ALLOWED_REACTION_EMOJI: frozenset[str] = frozenset(
    ["👍", "❤️", "😂", "😮", "😢", "🎉", "✅"]
)

# Default page size for cursor-based message lists. Cap matches the wallet
# service convention (Query(le=100)).
DEFAULT_MESSAGE_PAGE_SIZE = 50
MAX_MESSAGE_PAGE_SIZE = 100
