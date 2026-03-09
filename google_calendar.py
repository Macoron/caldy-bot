import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIALS_FILE = Path(".google_secrets/credentials.json")
TOKEN_FILE = Path(".google_secrets/token.json")


def get_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(prompt="consent")
            print(f"\nOpen this URL in your browser:\n{auth_url}\n")
            code = input("Paste the authorization code here: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials
        TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def register_tools(agent, tz: str):
    calendar_id = os.environ["GOOGLE_CALENDAR_ID"]

    @agent.tool_plain
    def list_upcoming_events(max_results: int = 10) -> str:
        """List upcoming events from Google Calendar."""
        logger.info("Tool called: list_upcoming_events")
        service = get_service()
        now = datetime.now(timezone.utc).isoformat()
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if not events:
            return "No upcoming events."
        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date"))
            lines.append(f"{start}: {e.get('summary', '(no title)')}")
        return "\n".join(lines)

    @agent.tool_plain
    def create_calendar_event(
        summary: str,
        start_datetime: str,
        end_datetime: str,
        description: str = "",
    ) -> str:
        """Create a Google Calendar event.
        start_datetime and end_datetime must be ISO 8601 format, e.g. '2024-03-15T14:00:00+01:00'.
        """
        logger.info("Tool called: create_calendar_event → %s", summary)
        service = get_service()
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_datetime, "timeZone": tz},
            "end": {"dateTime": end_datetime, "timeZone": tz},
        }
        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        return f"Event created: {created.get('htmlLink')}"

    @agent.tool_plain
    def delete_calendar_event(event_id: str) -> str:
        """Delete a Google Calendar event by its ID."""
        logger.info("Tool called: delete_calendar_event → %s", event_id)
        service = get_service()
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return f"Event {event_id} deleted."

    @agent.tool_plain
    def list_upcoming_events_with_ids(max_results: int = 10) -> str:
        """List upcoming events with their IDs (needed for deletion)."""
        logger.info("Tool called: list_upcoming_events_with_ids")
        service = get_service()
        now = datetime.now(timezone.utc).isoformat()
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if not events:
            return "No upcoming events."
        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date"))
            lines.append(f"[{e['id']}] {start}: {e.get('summary', '(no title)')}")
        return "\n".join(lines)
