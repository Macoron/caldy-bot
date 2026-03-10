import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import ModelRetry
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


class CalendarEvent(BaseModel):
    id: str
    summary: str
    start: str
    end: str
    location: Optional[str] = None
    description: Optional[str] = None


class CalendarEventResult(BaseModel):
    id: str
    summary: str
    link: str

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
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def register_tools(agent, tz: str, notify=None):
    calendar_id = os.environ["GOOGLE_CALENDAR_ID"]
    loop = asyncio.get_event_loop()

    def fire(msg: str):
        if notify:
            asyncio.run_coroutine_threadsafe(notify(msg), loop)

    @agent.tool_plain
    def create_calendar_event(
        summary: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
    ) -> CalendarEventResult:
        """Create a Google Calendar event."""
        logger.info("Tool called: create_calendar_event → %s", summary)
        try:
            service = get_service()
            created = service.events().insert(calendarId=calendar_id, body={
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
        }).execute()
            fire(f"✅ Event created: {summary}")
            return CalendarEventResult(id=created["id"], summary=created["summary"], link=created["htmlLink"])
        except Exception as e:
            logger.error("create_calendar_event failed: %s", e)
            raise ModelRetry(str(e))

    @agent.tool_plain
    def list_upcoming_events(max_results: int = 10) -> list[CalendarEvent]:
        """List upcoming events from Google Calendar. Returns event IDs which are required for update and delete operations."""
        logger.info("Tool called: list_upcoming_events")
        try:
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
            return [
                CalendarEvent(
                    id=e["id"],
                    summary=e.get("summary", "(no title)"),
                    start=e["start"].get("dateTime", e["start"].get("date")),
                    end=e["end"].get("dateTime", e["end"].get("date")),
                    location=e.get("location"),
                    description=e.get("description"),
                )
                for e in events
            ]
        except Exception as e:
            logger.error("list_upcoming_events failed: %s", e)
            raise ModelRetry(str(e))


    @agent.tool_plain
    def update_calendar_event(
        event_id: str,
        summary: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> CalendarEventResult:
        """Update fields of an existing Google Calendar event. Only provided fields are updated."""
        logger.info("Tool called: update_calendar_event → %s", event_id)
        try:
            service = get_service()
            event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            event.update({k: v for k, v in {
                "summary": summary,
                "description": description,
                "location": location,
                "start": {"dateTime": start.isoformat(), "timeZone": tz} if start else None,
                "end": {"dateTime": end.isoformat(), "timeZone": tz} if end else None,
            }.items() if v is not None})
            updated = service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
            fire(f"✏️ Event updated: {updated.get('summary')}")
            return CalendarEventResult(id=updated["id"], summary=updated["summary"], link=updated["htmlLink"])
        except Exception as e:
            logger.error("update_calendar_event failed: %s", e)
            raise ModelRetry(str(e))

    @agent.tool_plain
    def delete_calendar_event(event_id: str) -> str:
        """Delete a Google Calendar event by its ID."""
        logger.info("Tool called: delete_calendar_event → %s", event_id)
        try:
            service = get_service()
            event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            summary = event.get("summary", event_id)
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            fire(f"🗑 Event deleted: {summary}")
            return f"Event '{summary}' deleted."
        except Exception as e:
            logger.error("delete_calendar_event failed: %s", e)
            raise ModelRetry(str(e))