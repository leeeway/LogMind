"""add related_services to business_line

Revision ID: 20260424_02
Revises: -
Create Date: 2026-04-24 23:55:00

Adds related_services column to business_line table for
cross-service root cause correlation.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260424_02'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'business_line',
        sa.Column('related_services', sa.Text(), server_default='{}', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('business_line', 'related_services')
