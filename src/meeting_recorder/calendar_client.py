"""Google Calendar integration — OAuth and event fetching."""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meeting_recorder.config import (
    CALENDAR_LOOKAHEAD_MINUTES,
    CALENDAR_SCOPES,
    CONFIG_DIR,
    CREDENTIALS_PATH,
    TOKEN_PATH,
)

logger = logging.getLogger(__name__)


class CalendarEvent:
    """A simplified calendar event."""

    def __init__(self, title: str, attendees: list[str], start_time: datetime):
        self.title = title
        self.attendees = attendees
        self.start_time = start_time

    def __repr__(self):
        return f"CalendarEvent(title={self.title!r}, start={self.start_time})"


class CalendarClient:
    """Handles Google Calendar OAuth and event queries."""

    def __init__(self):
        self._service = None
        self._credentials = None

    def _ensure_config_dir(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_or_refresh_credentials(self):
        """Load saved credentials or trigger OAuth flow."""
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None

        if TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), CALENDAR_SCOPES)

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                logger.warning("Token refresh failed (likely 7-day test-mode expiry). Re-authenticating...")
                creds = None

        if not creds or not creds.valid:
            if not CREDENTIALS_PATH.exists():
                raise FileNotFoundError(
                    f"Google OAuth credentials not found at {CREDENTIALS_PATH}. "
                    "Please download credentials.json from Google Cloud Console."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), CALENDAR_SCOPES
            )
            creds = flow.run_local_server(port=0)

            # Save the token
            self._ensure_config_dir()
            TOKEN_PATH.write_text(creds.to_json())
            logger.info("OAuth token saved to %s", TOKEN_PATH)

        self._credentials = creds

    def _build_service(self):
        """Build the Google Calendar API service."""
        from googleapiclient.discovery import build

        self._load_or_refresh_credentials()
        self._service = build("calendar", "v3", credentials=self._credentials)

    def authenticate(self):
        """Run the OAuth flow (opens browser on first run)."""
        self._build_service()
        logger.info("Google Calendar authenticated successfully.")

    def get_upcoming_event(
        self, lookahead_minutes: int = CALENDAR_LOOKAHEAD_MINUTES
    ) -> CalendarEvent | None:
        """Find the next calendar event starting within lookahead_minutes.

        Checks all visible calendars to catch shared/work calendars.
        Returns the soonest event, or None.
        """
        if self._service is None:
            self._build_service()

        now = datetime.now(timezone.utc)
        window_end = now + timedelta(minutes=lookahead_minutes)

        now_iso = now.isoformat()
        end_iso = window_end.isoformat()

        best_event = None
        best_start = None

        try:
            # List all calendars visible to this account
            calendar_list = self._service.calendarList().list().execute()
            calendars = calendar_list.get("items", [])

            for cal in calendars:
                cal_id = cal["id"]
                try:
                    events_result = (
                        self._service.events()
                        .list(
                            calendarId=cal_id,
                            timeMin=now_iso,
                            timeMax=end_iso,
                            maxResults=5,
                            singleEvents=True,
                            orderBy="startTime",
                        )
                        .execute()
                    )
                except Exception:
                    logger.debug("Could not query calendar %s, skipping", cal_id)
                    continue

                for event in events_result.get("items", []):
                    start_str = event["start"].get("dateTime")
                    if not start_str:
                        continue  # Skip all-day events

                    start_dt = datetime.fromisoformat(start_str)
                    if best_start is None or start_dt < best_start:
                        title = event.get("summary", "Untitled Meeting")
                        attendees = [
                            a.get("email", "")
                            for a in event.get("attendees", [])
                            if a.get("email")
                        ]
                        best_event = CalendarEvent(
                            title=title,
                            attendees=attendees,
                            start_time=start_dt,
                        )
                        best_start = start_dt

        except Exception:
            logger.exception("Error fetching calendar events")
            return None

        if best_event:
            logger.info("Upcoming event: %s at %s", best_event.title, best_event.start_time)
        return best_event
