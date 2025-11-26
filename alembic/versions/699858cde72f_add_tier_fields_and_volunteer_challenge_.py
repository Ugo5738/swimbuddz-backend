"""add_tier_fields_and_volunteer_challenge_models

Revision ID: 699858cde72f
Revises: 2e5028f815c0
Create Date: 2025-11-25 18:47:42.747156

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '699858cde72f'
down_revision: Union[str, Sequence[str], None] = '2e5028f815c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add new tier-related columns to members table
    op.add_column('members', sa.Column('membership_tier', sa.String(), server_default='community', nullable=False))
    op.add_column('members', sa.Column('profile_photo_url', sa.String(), nullable=True))
    op.add_column('members', sa.Column('gender', sa.String(), nullable=True))
    op.add_column('members', sa.Column('date_of_birth', sa.DateTime(timezone=True), nullable=True))
    op.add_column('members', sa.Column('show_in_directory', sa.Boolean(), server_default='false', nullable=True))
    op.add_column('members', sa.Column('interest_tags', sa.ARRAY(sa.String()), nullable=True))
    op.add_column('members', sa.Column('club_badges_earned', sa.ARRAY(sa.String()), nullable=True))
    op.add_column('members', sa.Column('club_challenges_completed', sa.String(), nullable=True))
    op.add_column('members', sa.Column('punctuality_score', sa.Integer(), server_default='0', nullable=True))
    op.add_column('members', sa.Column('commitment_score', sa.Integer(), server_default='0', nullable=True))
    op.add_column('members', sa.Column('academy_skill_assessment', sa.String(), nullable=True))
    op.add_column('members', sa.Column('academy_goals', sa.String(), nullable=True))
    op.add_column('members', sa.Column('academy_preferred_coach_gender', sa.String(), nullable=True))
    op.add_column('members', sa.Column('academy_lesson_preference', sa.String(), nullable=True))
    op.add_column('members', sa.Column('academy_certifications', sa.ARRAY(sa.String()), nullable=True))
    op.add_column('members', sa.Column('academy_graduation_dates', sa.String(), nullable=True))

    # Create volunteer_roles table
    op.create_table(
        'volunteer_roles',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('slots_available', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create volunteer_interests table
    op.create_table(
        'volunteer_interests',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('role_id', sa.UUID(), nullable=False),
        sa.Column('member_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('notes', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create club_challenges table
    op.create_table(
        'club_challenges',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('challenge_type', sa.String(), nullable=False),
        sa.Column('badge_name', sa.String(), nullable=False),
        sa.Column('criteria_json', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create member_challenge_completions table
    op.create_table(
        'member_challenge_completions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('member_id', sa.UUID(), nullable=False),
        sa.Column('challenge_id', sa.UUID(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result_data', sa.String(), nullable=True),
        sa.Column('verified_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop new tables
    op.drop_table('member_challenge_completions')
    op.drop_table('club_challenges')
    op.drop_table('volunteer_interests')
    op.drop_table('volunteer_roles')

    # Remove new columns from members table
    op.drop_column('members', 'academy_graduation_dates')
    op.drop_column('members', 'academy_certifications')
    op.drop_column('members', 'academy_lesson_preference')
    op.drop_column('members', 'academy_preferred_coach_gender')
    op.drop_column('members', 'academy_goals')
    op.drop_column('members', 'academy_skill_assessment')
    op.drop_column('members', 'commitment_score')
    op.drop_column('members', 'punctuality_score')
    op.drop_column('members', 'club_challenges_completed')
    op.drop_column('members', 'club_badges_earned')
    op.drop_column('members', 'interest_tags')
    op.drop_column('members', 'show_in_directory')
    op.drop_column('members', 'date_of_birth')
    op.drop_column('members', 'gender')
    op.drop_column('members', 'profile_photo_url')
    op.drop_column('members', 'membership_tier')

