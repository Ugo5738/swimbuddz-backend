"""add_flywheel_metric_snapshot_tables

Revision ID: b8e1f3a52d40
Revises: 039dbdc232fe
Create Date: 2026-04-29 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b8e1f3a52d40'
down_revision = '039dbdc232fe'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create funnel_stage_enum postgres type
    funnel_stage_enum = postgresql.ENUM(
        'community_to_club',
        'club_to_academy',
        'community_to_academy',
        name='funnel_stage_enum',
    )
    funnel_stage_enum.create(op.get_bind(), checkfirst=True)

    # ── cohort_fill_snapshots ────────────────────────────────────────────────
    op.create_table(
        'cohort_fill_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cohort_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('cohort_name', sa.String(), nullable=False),
        sa.Column('program_name', sa.String(), nullable=True),
        sa.Column('capacity', sa.Integer(), nullable=False),
        sa.Column('active_enrollments', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pending_approvals', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('waitlist_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fill_rate', sa.Float(), nullable=False, server_default='0'),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cohort_status', sa.String(), nullable=False),
        sa.Column('days_until_start', sa.Integer(), nullable=True),
        sa.Column('snapshot_taken_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cohort_id', 'snapshot_taken_at', name='uq_cohort_fill_per_run'),
    )
    op.create_index(
        op.f('ix_cohort_fill_snapshots_cohort_id'),
        'cohort_fill_snapshots',
        ['cohort_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_cohort_fill_snapshots_snapshot_taken_at'),
        'cohort_fill_snapshots',
        ['snapshot_taken_at'],
        unique=False,
    )

    # ── funnel_conversion_snapshots ──────────────────────────────────────────
    op.create_table(
        'funnel_conversion_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'funnel_stage',
            postgresql.ENUM(
                'community_to_club',
                'club_to_academy',
                'community_to_academy',
                name='funnel_stage_enum',
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('cohort_period', sa.String(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('observation_window_days', sa.Integer(), nullable=False),
        sa.Column('source_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('converted_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('conversion_rate', sa.Float(), nullable=False, server_default='0'),
        sa.Column('breakdown_by_source', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('snapshot_taken_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'funnel_stage',
            'cohort_period',
            'snapshot_taken_at',
            name='uq_funnel_per_period_per_run',
        ),
    )
    op.create_index(
        op.f('ix_funnel_conversion_snapshots_funnel_stage'),
        'funnel_conversion_snapshots',
        ['funnel_stage'],
        unique=False,
    )
    op.create_index(
        op.f('ix_funnel_conversion_snapshots_cohort_period'),
        'funnel_conversion_snapshots',
        ['cohort_period'],
        unique=False,
    )

    # ── wallet_ecosystem_snapshots ───────────────────────────────────────────
    op.create_table(
        'wallet_ecosystem_snapshots',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('period_days', sa.Integer(), nullable=False),
        sa.Column('active_wallet_users', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('single_service_users', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cross_service_users', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cross_service_rate', sa.Float(), nullable=False, server_default='0'),
        sa.Column('total_bubbles_spent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_topup_bubbles', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('spend_distribution', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('snapshot_taken_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'period_start',
            'period_end',
            'snapshot_taken_at',
            name='uq_wallet_ecosystem_per_period_per_run',
        ),
    )
    op.create_index(
        op.f('ix_wallet_ecosystem_snapshots_snapshot_taken_at'),
        'wallet_ecosystem_snapshots',
        ['snapshot_taken_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_wallet_ecosystem_snapshots_snapshot_taken_at'),
        table_name='wallet_ecosystem_snapshots',
    )
    op.drop_table('wallet_ecosystem_snapshots')

    op.drop_index(
        op.f('ix_funnel_conversion_snapshots_cohort_period'),
        table_name='funnel_conversion_snapshots',
    )
    op.drop_index(
        op.f('ix_funnel_conversion_snapshots_funnel_stage'),
        table_name='funnel_conversion_snapshots',
    )
    op.drop_table('funnel_conversion_snapshots')

    op.drop_index(
        op.f('ix_cohort_fill_snapshots_snapshot_taken_at'),
        table_name='cohort_fill_snapshots',
    )
    op.drop_index(
        op.f('ix_cohort_fill_snapshots_cohort_id'),
        table_name='cohort_fill_snapshots',
    )
    op.drop_table('cohort_fill_snapshots')

    sa.Enum(name='funnel_stage_enum').drop(op.get_bind(), checkfirst=True)
