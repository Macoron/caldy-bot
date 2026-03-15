import asyncio
import logging
import os
from datetime import datetime, timezone, date, timedelta
from typing import Optional
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import ModelRetry
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def _friendly_date(d: date) -> str:
    today = date.today()
    if d == today:
        return "today"
    elif d == today + timedelta(days=1):
        return "tomorrow"
    elif today < d <= today + timedelta(days=6):
        return "this " + d.strftime("%A")
    elif today + timedelta(days=6) < d <= today + timedelta(days=13):
        return "next " + d.strftime("%A")
    elif d.year == today.year:
        return d.strftime("%B %-d")
    else:
        return d.strftime("%B %-d, %Y")


def _friendly_dt(dt: datetime) -> str:
    return f"{_friendly_date(dt.date())} at {dt.strftime('%H:%M')}"


def _friendly_event_time(dt_dict: dict) -> str:
    """Parse a Calendar API start/end dict and return a friendly label."""
    if "dateTime" in dt_dict:
        return _friendly_dt(datetime.fromisoformat(dt_dict["dateTime"]))
    return _friendly_date(date.fromisoformat(dt_dict["date"]))


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
        logger.info(
            "Tool called: create_calendar_event → %s | starts %s, ends %s",
            summary, _friendly_dt(start), _friendly_dt(end),
        )
        try:
            service = get_service()
            created = service.events().insert(calendarId=calendar_id, body={
            "summary": summary,
            "description": description,
            "location": location,
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
        }).execute()
            fire(f"✅ Event created: {summary} ({_friendly_dt(start)} – {_friendly_dt(end)})")
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
            start_str = _friendly_event_time(updated["start"])
            end_str = _friendly_event_time(updated["end"])
            logger.info("update_calendar_event result → %s | starts %s, ends %s", updated.get("summary"), start_str, end_str)
            fire(f"✏️ Event updated: {updated.get('summary')} ({start_str} – {end_str})")
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
            start_str = _friendly_event_time(event["start"])
            end_str = _friendly_event_time(event["end"])
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            logger.info("delete_calendar_event → %s | was %s – %s", summary, start_str, end_str)
            fire(f"🗑 Event deleted: {summary} ({start_str} – {end_str})")
            return f"Event '{summary}' deleted."
        except Exception as e:
            logger.error("delete_calendar_event failed: %s", e)
            raise ModelRetry(str(e))