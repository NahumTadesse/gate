"""Pin requests to their api key's org

Revision ID: d8c7cec64b98
Revises: 79ea6e333953
Create Date: 2026-09-22 18:25:19.980803

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8c7cec64b98"
down_revision: str | Sequence[str] | None = "79ea6e333953"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint("uq_api_keys_id_org_id", "api_keys", ["id", "org_id"])
    op.drop_constraint(
        "fk_requests_api_key_id_api_keys", "requests", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_requests_org_id_organizations", "requests", type_="foreignkey"
    )
    # Fails if any existing request names a key from another org, which is the
    # inconsistency this constraint exists to rule out.
    op.create_foreign_key(
        "fk_requests_api_key_id_org_id_api_keys",
        "requests",
        "api_keys",
        ["api_key_id", "org_id"],
        ["id", "org_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "fk_requests_api_key_id_org_id_api_keys", "requests", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_requests_org_id_organizations",
        "requests",
        "organizations",
        ["org_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_requests_api_key_id_api_keys",
        "requests",
        "api_keys",
        ["api_key_id"],
        ["id"],
    )
    op.drop_constraint("uq_api_keys_id_org_id", "api_keys", type_="unique")
