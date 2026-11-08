# Repairing latin1 Persian storage

## Cause and scope

Both the original ORM tables and revision `0001_initial` lacked MySQL table charset
options. MariaDB inherited the database default (`latin1` on the reported server).
`charset=utf8mb4` in a connection URL controls the client protocol, not table storage.
The diagnostic SELECT echo is not a write/read test of a stored column. The reported
connection is actually `utf8mb3`, not full Unicode for supplementary characters/emoji.

Revision `0002_unicode_storage` changes the default and every text/string column of
`personas` and `ai_message_log` to `utf8mb4` / `utf8mb4_unicode_ci`. It uses explicit
MODIFY clauses to preserve VARCHAR lengths, TEXT types and nullability. It leaves
numeric IDs, indexes, unique constraints and foreign keys in place. Changing collation
can expose duplicate unique keys; stage the migration against a backup first.
Existing TEXT content must fit its byte limit after conversion; do not run conversion
with permissive/truncating SQL modes. Expect table rebuilds/locks and additional disk
space. MariaDB DDL implicitly commits: the two table changes are not one rollbackable
transaction. If the second fails, fix the cause and rerun; the ALTERs are repeatable.

All four ORM models now specify `mysql_charset='utf8mb4'` and
`mysql_collate='utf8mb4_unicode_ci'`. The initial migration has also been amended
**for fresh installs only**, adding those create_table options. Changing its file does
not alter an existing database: those installations need revision 0002. Existing
`users` and `business_connections` are outside the repair migration's scope and retain
their current storage settings. Future create_table migrations must carry the same
explicit options; inspect generated migrations rather than relying on server defaults.

The bot and Alembic now normalize MySQL/MariaDB connection URLs to `charset=utf8mb4`.
Still update the server `.env` to include `?charset=utf8mb4` (or `&charset=utf8mb4`
if it already has other query parameters), then restart the bot after migration.

## Production procedure

1. Pause/stop the bot and watchdog cron. Take a real, tested database backup and
   validate the migration on a restored staging copy first. The backup shell script
   in this repository is still a placeholder.
2. Deploy the updated code and install requirements with the cPanel virtualenv.
3. From the repository root, inspect the revision:
   ```bash
   PY="$HOME/virtualenv/filo/3.11/bin/python3"
   "$PY" -m alembic current
   ```
   If the tables were created by create_all and have no Alembic revision, first
   verify the schema matches 0001 (allowing the known latin1 charset difference),
   then run `"$PY" -m alembic stamp 0001_initial`. Do not stamp head: that skips repair.
   Do not run the initial table-creation upgrade against existing tables.
4. With the connection charset configured, apply and verify:
   ```bash
   "$PY" -m alembic upgrade head
   "$PY" -m alembic current
   "$PY" -m alembic check
   "$PY" -m tools.check_encoding --ai
   ```
   The revision should be the current head (now 0003_setup_progress), with 0002_unicode_storage applied. Target column charsets and the first
   three connection charset values should be utf8mb4. The database default may still
   be latin1; the repaired tables no longer inherit it. SQLite skips the charset DDL.
5. Re-enter damaged persona values. `/setname`, `/setrole`, `/settone`, `/setinfo`
   and `/setrules` replace the corresponding fields. There is currently no command
   to edit signature/language; if those fields were damaged, repair them via a
   controlled owner-specific DB update, not a bulk overwrite.
6. Restart one bot process and restore cron. In draft mode, test storing Persian
   and emoji, retrieve the draft with /drafts, then approve it once from a test account.

## Lost data and rollback

Actual `?` replacements are irreversible; the original letters are no longer stored.
Restore them from a known-good backup or re-enter them manually after the migration.
Check role, tone, knowledge, instructions and signature too; the diagnostic inspected
only a subset. Do not automatically replace all question marks: some are legitimate.

No lossy downgrade to latin1 is provided for MySQL/MariaDB. Restore a verified backup
or plan a specific data-safe rollback. SQLite downgrade is a no-op for this revision.
This migration has local SQLite roundtrip and offline MySQL SQL tests; it has not
been executed against the production server or a live MariaDB instance here.
