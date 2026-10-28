"""Fix checkmating moves stored as blunders.

Before this fix, "mate in 0" after a mating move was scored as a loss for the mover, so every
checkmating move got wc_after near -1 and label 'blunder'. A mating move's mover always wins:
set wc_after to the mate win-chance value and clear the label (no drop is possible from it).
"""

import math

from alembic import op

revision = "d1e7a3c5b902"
down_revision = "c9b4e2a10640"

# win_chances for a mate score: cp = (21 - 0) * 100 = 2100 from the mover's view.
MATE_WC = 2 / (1 + math.exp(-0.00368208 * 2100)) - 1


def upgrade() -> None:
    op.execute(f"UPDATE moves SET wc_after = {MATE_WC!r}, label = NULL WHERE san LIKE '%#'")


def downgrade() -> None:
    pass
