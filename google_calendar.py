import asyncio
import logging
import os
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import ModelRetry
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from utils import _friendly_dt, _friendly_rrule, _friendly_event_time

logger = logging.getLogger(__name__)


class CalendarConflictError(Exception):
    def __init__(self, conflicts: list[str]):
        self.conflicts = conflicts
        super().__init__("Conflicts: " + ", ".join(conflicts))


def _check_conflicts(service, calendar_id: str, start: datetime, end: datetime,
                     ignore: bool, exclude_event_id: str | None = None):
    if ignore:
        return
    result = service.events().list(
        calendarId=calendar_id,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    conflicts = [
        f"{e.get('summary', '(no title)')} ({_friendly_event_time(e['start'])} – {_friendly_event_time(e['end'])})"
        for e in result.get("items", [])
        if e["id"] != exclude_event_id
    ]
    if conflicts:
        raise CalendarConflictError(conflicts)


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


def _ensure_tz(dt: Optional[datetime], tz_info) -> Optional[datetime]:
    """Attach configured timezone if the model returned a naive datetime."""
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz_info)
    return dt


def register_tools(agent, tz: str, notify=None):
    calendar_id = os.environ["GOOGLE_CALENDAR_ID"]
    tz_info = ZoneInfo(tz)
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
        recurrence: Optional[list[str]] = None,
        ignore_conflicts: bool = False,
    ) -> CalendarEventResult:
        """Create a Google Calendar event.

        For recurring events, pass recurrence as a list of RFC 5545 RRULE strings, e.g.:
          - Every day: ["RRULE:FREQ=DAILY"]
          - Every weekday: ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]
          - Every Monday for 4 weeks: ["RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=4"]
          - Every month on the 1st: ["RRULE:FREQ=MONTHLY;BYMONTHDAY=1"]
          - Every year until a date: ["RRULE:FREQ=YEARLY;UNTIL=20271231T000000Z"]

        Set ignore_conflicts=True only when the user has explicitly acknowledged the conflict and
        wants to create the event anyway.
        """
        start = _ensure_tz(start, tz_info)
        end = _ensure_tz(end, tz_info)
        logger.info(
            "Tool called: create_calendar_event → %s | starts %s, ends %s%s",
            summary, _friendly_dt(start), _friendly_dt(end),
            f" | recurrence: {recurrence}" if recurrence else "",
        )
        try:
            service = get_service()
            _check_conflicts(service, calendar_id, start, end, ignore=ignore_conflicts)
            body = {
                "summary": summary,
                "description": description,
                "location": location,
                "start": {"dateTime": start.isoformat(), "timeZone": tz},
                "end": {"dateTime": end.isoformat(), "timeZone": tz},
            }
            if recurrence:
                body["recurrence"] = recurrence
            created = service.events().insert(calendarId=calendar_id, body=body).execute()
            recurrence_str = f", {_friendly_rrule(recurrence)}" if recurrence else ""
            fire(f"✅ Event created: {summary} ({_friendly_dt(start)} – {_friendly_dt(end)}{recurrence_str})")
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
        recurrence: Optional[list[str]] = None,
        ignore_conflicts: bool = False,
    ) -> CalendarEventResult:
        """Update fields of an existing Google Calendar event. Only provided fields are updated.

        To change or add recurrence, pass recurrence as a list of RFC 5545 RRULE strings (same
        format as create_calendar_event). To remove recurrence, pass recurrence=[].

        Set ignore_conflicts=True only when the user has explicitly acknowledged the conflict and
        wants to reschedule anyway.
        """
        start = _ensure_tz(start, tz_info)
        end = _ensure_tz(end, tz_info)
        logger.info("Tool called: update_calendar_event → %s", event_id)
        try:
            service = get_service()
            event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            if start and end:
                _check_conflicts(service, calendar_id, start, end, ignore=ignore_conflicts, exclude_event_id=event_id)
            event.update({k: v for k, v in {
                "summary": summary,
                "description": description,
                "location": location,
                "start": {"dateTime": start.isoformat(), "timeZone": tz} if start else None,
                "end": {"dateTime": end.isoformat(), "timeZone": tz} if end else None,
            }.items() if v is not None})
            if recurrence is not None:
                event["recurrence"] = recurrence
            updated = service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
            start_str = _friendly_event_time(updated["start"])
            end_str = _friendly_event_time(updated["end"])
            recurrence_str = f", {_friendly_rrule(updated['recurrence'])}" if updated.get("recurrence") else ""
            logger.info("update_calendar_event result → %s | starts %s, ends %s%s", updated.get("summary"), start_str, end_str, recurrence_str)
            fire(f"✏️ Event updated: {updated.get('summary')} ({start_str} – {end_str}{recurrence_str})")
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


# --- Reminder loop ---

def _load_notified(path: Path) -> dict[str, str]:
    """Returns {event_id: iso_start_time}"""
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_notified(path: Path, notified: dict[str, str]):
    path.write_text(json.dumps(notified, indent=2))


def _prune_notified(notified: dict) -> dict:
    """Remove entries older than 24 hours (based on when the reminder was sent)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    return {
        eid: entry for eid, entry in notified.items()
        if datetime.fromisoformat(entry["notified_at"]) > cutoff
    }


async def reminder_loop(bot, chat_id: int, calendar_id: str, tz, config):
    notified_path = Path(config.notified_file)
    notified = _load_notified(notified_path)

    logger.info("Reminder loop started | calendar=%s, poll=%dmin, remind=%dmin ahead",
                calendar_id, config.poll_interval_minutes, config.reminder_minutes)

    while True:
        try:
            now = datetime.now(timezone.utc)
            window_end = now + timedelta(minutes=config.reminder_minutes)

            service = get_service()
            events = service.events().list(
                calendarId=calendar_id,
                timeMin=now.isoformat(),
                timeMax=window_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            ).execute().get("items", [])

            logger.debug("Reminder poll: %d events in next %d min, %d already notified",
                         len(events), config.reminder_minutes, len(notified))

            for event in events:
                event_id = event["id"]
                start_str = event["start"].get("dateTime") or event["start"].get("date")
                prev = notified.get(event_id)
                if prev and prev["start"] == start_str:
                    continue
                summary   = event.get("summary", "(No title)")
                location  = event.get("location", "")
                loc_line  = f"\n📍 {location}" if location else ""
                start_dt  = datetime.fromisoformat(start_str).astimezone(tz)
                time_str  = start_dt.strftime("%H:%M")
                text = f"⏰ Reminder: *{summary}* starts at {time_str}{loc_line}"
                await bot.send_message(chat_id, text, parse_mode="Markdown")
                notified[event_id] = {
                    "start": start_str,
                    "notified_at": datetime.now(timezone.utc).isoformat(),
                }
                logger.info("Sent reminder for event %s (%s at %s)", event_id, summary, time_str)

            notified = _prune_notified(notified)
            _save_notified(notified_path, notified)

        except Exception:
            logger.exception("Error in reminder loop")

        await asyncio.sleep(config.poll_interval_minutes * 60)