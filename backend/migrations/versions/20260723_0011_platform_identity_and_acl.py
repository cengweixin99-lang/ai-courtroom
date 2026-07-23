"""add platform identity, organization ACLs, and session owners

Revision ID: 20260723_0011
Revises: 20260721_0010
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0011"
down_revision: str | None = "20260721_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PUBLIC_TRAINING_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000001"
LEGACY_SYSTEM_SUBJECT = "system:legacy-import"


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_organizations_slug"), "organizations", ["slug"], unique=True)
    op.create_table(
        "platform_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("auth_subject", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("auth_subject"),
    )
    op.create_index(
        op.f("ix_platform_users_auth_subject"), "platform_users", ["auth_subject"], unique=True
    )
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="learner"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["platform_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id"),
    )
    op.create_index(
        op.f("ix_organization_memberships_organization_id"),
        "organization_memberships",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_organization_memberships_user_id"),
        "organization_memberships",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "case_access_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("access_level", sa.String(length=32), nullable=False, server_default="use"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["package_id"], ["case_packages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("package_id", "organization_id"),
    )
    op.create_index(
        op.f("ix_case_access_grants_package_id"), "case_access_grants", ["package_id"], unique=False
    )
    op.create_index(
        op.f("ix_case_access_grants_organization_id"),
        "case_access_grants",
        ["organization_id"],
        unique=False,
    )
    op.add_column("court_sessions", sa.Column("owner_user_id", sa.Integer(), nullable=True))
    op.create_index(
        op.f("ix_court_sessions_owner_user_id"), "court_sessions", ["owner_user_id"], unique=False
    )
    op.create_foreign_key(
        "fk_court_sessions_owner_user_id_platform_users",
        "court_sessions",
        "platform_users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Existing imported packages remain available after the migration.  New users
    # are automatically enrolled in this shared training organization on first sign-in.
    op.execute(
        sa.text(
            "INSERT INTO organizations (id, slug, name) VALUES "
            "(:id, 'public-training', 'Public Training')"
        ).bindparams(id=PUBLIC_TRAINING_ORGANIZATION_ID)
    )
    op.execute(
        sa.text(
            "INSERT INTO platform_users (auth_subject, display_name) VALUES "
            "(:subject, 'Legacy system')"
        ).bindparams(subject=LEGACY_SYSTEM_SUBJECT)
    )
    op.execute(
        sa.text(
            "INSERT INTO organization_memberships (organization_id, user_id, role) "
            "SELECT :organization_id, id, 'admin' FROM platform_users "
            "WHERE auth_subject = :subject"
        ).bindparams(organization_id=PUBLIC_TRAINING_ORGANIZATION_ID, subject=LEGACY_SYSTEM_SUBJECT)
    )
    op.execute(
        sa.text(
            "INSERT INTO case_access_grants (package_id, organization_id, access_level) "
            "SELECT id, :organization_id, 'use' FROM case_packages"
        ).bindparams(organization_id=PUBLIC_TRAINING_ORGANIZATION_ID)
    )
    op.execute(
        sa.text(
            "UPDATE court_sessions SET owner_user_id = ("
            "SELECT id FROM platform_users WHERE auth_subject = :subject) "
            "WHERE owner_user_id IS NULL"
        ).bindparams(subject=LEGACY_SYSTEM_SUBJECT)
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_court_sessions_owner_user_id_platform_users", "court_sessions", type_="foreignkey"
    )
    op.drop_index(op.f("ix_court_sessions_owner_user_id"), table_name="court_sessions")
    op.drop_column("court_sessions", "owner_user_id")
    op.drop_index(op.f("ix_case_access_grants_organization_id"), table_name="case_access_grants")
    op.drop_index(op.f("ix_case_access_grants_package_id"), table_name="case_access_grants")
    op.drop_table("case_access_grants")
    op.drop_index(
        op.f("ix_organization_memberships_user_id"), table_name="organization_memberships"
    )
    op.drop_index(
        op.f("ix_organization_memberships_organization_id"), table_name="organization_memberships"
    )
    op.drop_table("organization_memberships")
    op.drop_index(op.f("ix_platform_users_auth_subject"), table_name="platform_users")
    op.drop_table("platform_users")
    op.drop_index(op.f("ix_organizations_slug"), table_name="organizations")
    op.drop_table("organizations")
