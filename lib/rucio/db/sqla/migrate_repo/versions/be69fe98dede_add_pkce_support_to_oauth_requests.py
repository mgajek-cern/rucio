# Copyright European Organization for Nuclear Research (CERN) since 2012
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""add_pkce_support_to_oauth_requests"""    # noqa: D400, D415

import sqlalchemy as sa
from alembic import context
from alembic.op import add_column, drop_column

# Alembic revision identifiers
revision = 'be69fe98dede'
down_revision = '3b943000da18'


def upgrade():
    if context.get_context().dialect.name in ['oracle', 'postgresql', 'mysql']:
        schema = context.get_context().version_table_schema or ''
        add_column('oauth_requests', sa.Column('code_verifier', sa.String(128), nullable=True), schema=schema)

def downgrade():
    if context.get_context().dialect.name in ['oracle', 'postgresql', 'mysql']:
        schema = context.get_context().version_table_schema or ''
        drop_column('oauth_requests', 'code_verifier', schema=schema)
