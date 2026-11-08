"""Run: python -m tools.check_encoding [--ai]. Prints no message content or credentials."""
import argparse
import asyncio
import json

from sqlalchemy import text

from core.config import get_settings
from database.connection import get_engine
from services.ai import generate_reply


def stats(value: str) -> dict:
    return {
        "characters": len(value),
        "question_marks": value.count("?"),
        "arabic_script_characters": sum("\u0600" <= c <= "\u06ff" for c in value),
        "replacement_characters": value.count("\ufffd"),
    }


async def main(check_ai: bool) -> None:
    # Escapes make this probe independent of terminal/file Persian encoding.
    probe = "\u0633\u0644\u0627\u0645 \U0001f331"
    print("local_probe:", json.dumps(stats(probe)))
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            if conn.dialect.name == "mysql":
                charset = (await conn.execute(text(
                    "SELECT @@character_set_client, @@character_set_connection, "
                    "@@character_set_results, @@character_set_database"
                ))).one()
                print("database_charsets:", json.dumps(list(charset)))
                columns = (await conn.execute(text(
                    "SELECT TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                    "AND TABLE_NAME IN ('personas', 'ai_message_log') "
                    "AND CHARACTER_SET_NAME IS NOT NULL"
                ))).all()
                print("column_charsets:", json.dumps([list(row) for row in columns]))
            echoed = (await conn.execute(text("SELECT :probe"), {"probe": probe})).scalar_one()
            print("database_connection_roundtrip_ok:", echoed == probe)
            # Aggregate evidence only: no persona/draft content or owner IDs printed.
            for table, column in (("personas", "knowledge_base"), ("personas", "instructions"),
                                  ("personas", "name"), ("ai_message_log", "draft_text")):
                result = await conn.stream(text(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"))
                rows = suspicious = 0
                async for row in result:
                    value = row[0]
                    rows += 1
                    # Heuristic evidence only; question marks can be legitimate.
                    suspicious += value.count("?") >= 4
                print(f"stored_{table}_{column}:", json.dumps({
                    "rows": rows, "rows_with_four_or_more_question_marks": suspicious}))
    except Exception as exc:
        print("database_check_failed_type:", type(exc).__name__)
    finally:
        await engine.dispose()
    if check_ai:
        if not get_settings().GEMINI_API_KEY:
            print("ai_check: missing_api_key")
            return
        try:
            reply = await generate_reply(
                "Reply in Persian with a short greeting. Do not use question marks.", probe)
            print("direct_ai_response:", json.dumps(stats(reply)))
        except Exception as exc:
            print("ai_check_failed_type:", type(exc).__name__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ai", action="store_true", help="Makes one real AI call (provider quota/cost applies)")
    asyncio.run(main(parser.parse_args().ai))
