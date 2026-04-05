"""Notion integration — push meeting records to a Notion database."""

import logging
import re

from meeting_recorder.config import NOTION_DATABASE_ID, NOTION_TOKEN_PATH

logger = logging.getLogger(__name__)


def _read_token() -> str | None:
    """Read the Notion integration token from disk."""
    if not NOTION_TOKEN_PATH.exists():
        return None
    return NOTION_TOKEN_PATH.read_text().strip()


def _extract_action_items(summary: str) -> str:
    """Pull action items from the summary text."""
    lines = summary.split("\n")
    collecting = False
    items = []
    for line in lines:
        if re.search(r"action\s*items?", line, re.IGNORECASE):
            collecting = True
            # If the action items are on the same line after a colon
            after_colon = line.split(":", 1)
            if len(after_colon) > 1 and after_colon[1].strip():
                items.append(after_colon[1].strip())
            continue
        if collecting:
            stripped = line.strip()
            if stripped.startswith(("-", "*", "•")):
                items.append(stripped.lstrip("-*• "))
            elif stripped and not stripped.startswith("#"):
                items.append(stripped)
            elif not stripped and items:
                break  # blank line after items = end of section
            elif stripped.startswith("#") or re.search(r"\*\*[A-Z]", stripped):
                break  # new section header
    return "\n".join(f"- {item}" for item in items) if items else ""


def push_to_notion(
    title: str,
    start_time,
    attendees: list[str],
    summary: str | None,
    transcript_path=None,
) -> bool:
    """Create a row in the Notion meetings database.

    Returns True on success, False on failure.
    """
    token = _read_token()
    if not token:
        logger.info("Notion token not configured — skipping sync.")
        return False

    try:
        from notion_client import Client

        notion = Client(auth=token)

        # Build properties
        properties = {
            "Call Title": {"title": [{"text": {"content": title}}]},
            "Date": {
                "date": {
                    "start": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            },
        }

        if attendees:
            properties["Attendees"] = {
                "rich_text": [{"text": {"content": ", ".join(attendees)}}]
            }

        if summary:
            # Notion rich_text has a 2000 char limit per block
            summary_text = summary[:2000]
            properties["Summary"] = {
                "rich_text": [{"text": {"content": summary_text}}]
            }

            action_items = _extract_action_items(summary)
            if action_items:
                properties["Action Items"] = {
                    "rich_text": [{"text": {"content": action_items[:2000]}}]
                }

        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties,
        )

        logger.info("Meeting record pushed to Notion: %s", title)
        return True

    except Exception:
        logger.exception("Failed to push to Notion")
        return False
