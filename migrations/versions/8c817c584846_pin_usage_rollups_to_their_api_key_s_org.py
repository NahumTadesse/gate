"""Pin usage rollups to their api key's org

Revision ID: 8c817c584846
Revises: d8c7cec64b98
Create Date: 2026-09-22 18:26:32.993103

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8c817c584846"
down_revision: str | Sequence[str] | None = "d8c7cec64b98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(
        "fk_usage_rollups_api_key_id_api_keys", "usage_rollups", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_usage_rollups_org_id_organizations", "usage_rollups", type_="foreignkey"
    )
    # Fails if any existing rollup names a key from another org, which is the
    # inconsistency this constraint exists to rule out.
    op.create_foreign_key(
        "fk_usage_rollups_api_key_id_org_id_api_keys",
        "usage_rollups",
        "api_keys",
        ["api_key_id", "org_id"],
        ["id", "org_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "fk_usage_rollups_api_key_id_org_id_api_keys",
        "usage_rollups",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_usage_rollups_api_key_id_api_keys",
        "usage_rollups",
        "api_keys",
        ["api_key_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_usage_rollups_org_id_organizations",
        "usage_rollups",
        "organizations",
        ["org_id"],
        ["id"],
    )
