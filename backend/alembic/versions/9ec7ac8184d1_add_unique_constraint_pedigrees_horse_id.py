"""add unique constraint to pedigrees horse_id

Revision ID: 9ec7ac8184d1
Revises: 0001
Create Date: 2026-03-22

pedigrees.horse_id に UNIQUE 制約と INDEX を追加する。
1頭につき1レコードを保証し、PedigreeImporter の ON CONFLICT UPSERT を有効にする。
"""
from typing import Sequence, Union

from alembic import op


revision: str = '9ec7ac8184d1'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_keiba_pedigrees_horse_id',
        'pedigrees',
        ['horse_id'],
        unique=True,
        schema='keiba',
    )
    op.create_unique_constraint(
        'uq_pedigree_horse_id',
        'pedigrees',
        ['horse_id'],
        schema='keiba',
    )


def downgrade() -> None:
    op.drop_constraint('uq_pedigree_horse_id', 'pedigrees', schema='keiba')
    op.drop_index('ix_keiba_pedigrees_horse_id', table_name='pedigrees', schema='keiba')
