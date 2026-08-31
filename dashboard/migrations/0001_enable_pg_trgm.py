"""Enable the pg_trgm extension.

Design §4.7 builds ``idx_leads_search_trgm`` as a GIN index using
``gin_trgm_ops`` over the four searchable Lead columns, because the
case-insensitive *substring* search of Requirement 2.3 cannot be served by a
B-tree. The operator class only exists once ``pg_trgm`` is installed, so the
extension has to be created before any model or index migration runs — hence it
is the first operation of the app's first migration, with no model state
attached.

``TrigramExtension`` is ``CreateExtension("pg_trgm")``; it is reversible and
issues ``CREATE EXTENSION IF NOT EXISTS``, so re-running against a database that
already carries the extension is a no-op. Creating an extension requires a
superuser or a role holding CREATE on the database, which is a migration-time
privilege and not the restricted application role of §3.1/§3.7.3.
"""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        TrigramExtension(),
    ]
