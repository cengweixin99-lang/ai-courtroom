"""add case package lifecycle and import audit

Revision ID: 20260723_0012
Revises: 20260723_0011
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0012"
down_revision: str | None = "20260723_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "case_packages",
        sa.Column(
            "lifecycle_status", sa.String(length=32), nullable=False, server_default="published"
        ),
    )
    op.add_column("case_packages", sa.Column("source_filename", sa.String(255), nullable=True))
    op.add_column("case_packages", sa.Column("source_sha256", sa.String(64), nullable=True))
    op.add_column("case_packages", sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True))
    op.add_column(
        "case_packages", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        op.f("ix_case_packages_lifecycle_status"),
        "case_packages",
        ["lifecycle_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_packages_uploaded_by_user_id"),
        "case_packages",
        ["uploaded_by_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_case_packages_uploaded_by_user_id_platform_users",
        "case_packages",
        "platform_users",
        ["uploaded_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "case_import_attempts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("package_id", sa.Integer(), nullable=True),
        sa.Column("source_filename", sa.String(255), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("archive_size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["platform_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["package_id"], ["case_packages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_case_import_attempts_actor_user_id"),
        "case_import_attempts",
        ["actor_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_import_attempts_package_id"),
        "case_import_attempts",
        ["package_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_import_attempts_source_sha256"),
        "case_import_attempts",
        ["source_sha256"],
        unique=False,
    )
    op.create_index(
        op.f("ix_case_import_attempts_status"), "case_import_attempts", ["status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_case_import_attempts_status"), table_name="case_import_attempts")
    op.drop_index(op.f("ix_case_import_attempts_source_sha256"), table_name="case_import_attempts")
    op.drop_index(op.f("ix_case_import_attempts_package_id"), table_name="case_import_attempts")
    op.drop_index(op.f("ix_case_import_attempts_actor_user_id"), table_name="case_import_attempts")
    op.drop_table("case_import_attempts")
    op.drop_constraint(
        "fk_case_packages_uploaded_by_user_id_platform_users", "case_packages", type_="foreignkey"
    )
    op.drop_index(op.f("ix_case_packages_uploaded_by_user_id"), table_name="case_packages")
    op.drop_index(op.f("ix_case_packages_lifecycle_status"), table_name="case_packages")
    op.drop_column("case_packages", "published_at")
    op.drop_column("case_packages", "uploaded_by_user_id")
    op.drop_column("case_packages", "source_sha256")
    op.drop_column("case_packages", "source_filename")
    op.drop_column("case_packages", "lifecycle_status")
