"""Add api keys, requests, usage rollups and model prices

Revision ID: 79ea6e333953
Revises: 8ac8cdbfc829
Create Date: 2026-09-22 18:21:17.969361

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "79ea6e333953"
down_revision: str | Sequence[str] | None = "8ac8cdbfc829"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column(
            "rpm_limit", sa.Integer(), server_default=sa.text("60"), nullable=False
        ),
        sa.Column("monthly_budget_micros", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_api_keys_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_index(op.f("ix_api_keys_org_id"), "api_keys", ["org_id"])

    op.create_table(
        "requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column(
            "prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cost_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("streamed", sa.Boolean(), server_default=sa.false(), nullable=False),
        created_at(),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_requests_org_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["api_keys.id"],
            name=op.f("fk_requests_api_key_id_api_keys"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requests")),
        sa.UniqueConstraint("request_id", name=op.f("uq_requests_request_id")),
    )
    op.create_index(
        "ix_requests_org_id_created_at",
        "requests",
        ["org_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_requests_api_key_id_created_at",
        "requests",
        ["api_key_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "usage_rollups",
        sa.Column("api_key_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column(
            "request_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "cost_micros", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.CheckConstraint(
            "extract(epoch FROM bucket_start) % 3600 = 0",
            name=op.f("ck_usage_rollups_bucket_start_hour"),
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["api_keys.id"],
            name=op.f("fk_usage_rollups_api_key_id_api_keys"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_usage_rollups_org_id_organizations"),
        ),
        sa.PrimaryKeyConstraint(
            "api_key_id", "model", "bucket_start", name=op.f("pk_usage_rollups")
        ),
    )

    op.create_table(
        "model_prices",
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_micros_per_1k", sa.BigInteger(), nullable=False),
        sa.Column("output_micros_per_1k", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "model", "effective_from", name=op.f("pk_model_prices")
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("model_prices")
    op.drop_table("usage_rollups")
    op.drop_index("ix_requests_api_key_id_created_at", table_name="requests")
    op.drop_index("ix_requests_org_id_created_at", table_name="requests")
    op.drop_table("requests")
    op.drop_index(op.f("ix_api_keys_org_id"), table_name="api_keys")
    op.drop_table("api_keys")
