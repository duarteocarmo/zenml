"""Increase step_configuration length [9d8020441014].

Revision ID: 9d8020441014
Revises: 0.35.1
Create Date: 2023-03-16 16:07:09.596900

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMTEXT

# revision identifiers, used by Alembic.
revision = "9d8020441014"
down_revision = "0.35.1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade database schema and/or data, creating a new revision."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("pipeline_deployment", schema=None) as batch_op:
        batch_op.alter_column(
            "step_configurations",
            existing_type=sa.TEXT(),
            type_=sa.String(length=16777215).with_variant(MEDIUMTEXT, "mysql"),
            existing_nullable=False,
        )

    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade database schema and/or data back to the previous revision."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("pipeline_deployment", schema=None) as batch_op:
        batch_op.alter_column(
            "step_configurations",
            existing_type=sa.String(length=16777215),
            type_=sa.TEXT(),
            existing_nullable=False,
        )

    # ### end Alembic commands ###