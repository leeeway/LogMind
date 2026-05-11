"""add noise_patterns to business_line

Revision ID: 20260511_01
Revises: 20260424_02
Create Date: 2026-05-11 20:50:00

Adds noise_patterns column to business_line table for per-business-line
custom noise pattern configuration. Operators can define keyword patterns
that classify business flow logs (user input errors, rate limiting, etc.)
as noise rather than real faults.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260511_01'
down_revision = '20260424_02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'business_line',
        sa.Column('noise_patterns', sa.Text(), server_default='[]', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('business_line', 'noise_patterns')
