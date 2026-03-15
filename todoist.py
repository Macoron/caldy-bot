import asyncio
import logging
import os
from datetime import date
from typing import Optional

from pydantic_ai import ModelRetry
from todoist_api_python.api import TodoistAPI

from utils import _friendly_date

logger = logging.getLogger(__name__)


def register_tools(agent, tz: str, notify=None):
    api = TodoistAPI(os.environ["TODOIST_API_TOKEN"])
    loop = asyncio.get_event_loop()

    def fire(msg: str):
        if notify:
            asyncio.run_coroutine_threadsafe(notify(msg), loop)

    def _get_all_projects() -> list:
        results = []
        for page in api.get_projects():
            results.extend(page)
        return results

    def _get_all_sections(project_id: str | None = None) -> list:
        results = []
        kwargs = {"project_id": project_id} if project_id else {}
        for page in api.get_sections(**kwargs):
            results.extend(page)
        return results

    def _get_all_tasks(**kwargs) -> list:
        results = []
        for page in api.get_tasks(**kwargs):
            results.extend(page)
        return results

    def _resolve_project(name: str) -> str:
        projects = _get_all_projects()
        for p in projects:
            if p.name.lower() == name.lower():
                return p.id
        available = ", ".join(p.name for p in projects)
        raise ModelRetry(f"Project '{name}' not found. Available projects: {available}")

    def _resolve_section(name: str, project_id: str) -> str:
        sections = _get_all_sections(project_id)
        for s in sections:
            if s.name.lower() == name.lower():
                return s.id
        available = ", ".join(s.name for s in sections)
        raise ModelRetry(f"Section '{name}' not found. Available sections: {available}")

    def _build_project_map() -> dict[str, str]:
        return {p.id: p.name for p in _get_all_projects()}

    def _build_section_map(project_id: str | None = None) -> dict[str, str]:
        return {s.id: s.name for s in _get_all_sections(project_id)}

    def _task_to_dict(task, project_map: dict, section_map: dict) -> dict:
        return {
            "id": task.id,
            "content": task.content,
            "description": task.description,
            "project_name": project_map.get(task.project_id, task.project_id),
            "section_name": section_map.get(task.section_id) if task.section_id else None,
            "due_date": task.due.date.isoformat() if task.due else None,
            "is_completed": task.is_completed,
            "priority": task.priority,
        }

    @agent.tool_plain
    def list_todoist_projects() -> list[dict]:
        """List all Todoist projects. Returns project id and name."""
        logger.info("Tool called: list_todoist_projects")
        try:
            projects = _get_all_projects()
            return [{"id": p.id, "name": p.name} for p in projects]
        except Exception as e:
            logger.error("list_todoist_projects failed: %s", e)
            raise ModelRetry(str(e))

    @agent.tool_plain
    def list_todoist_sections(project_name: Optional[str] = None) -> list[dict]:
        """List Todoist sections, optionally filtered by project name."""
        logger.info("Tool called: list_todoist_sections | project=%s", project_name)
        try:
            project_id = _resolve_project(project_name) if project_name else None
            project_map = _build_project_map()
            sections = _get_all_sections(project_id)
            return [
                {"id": s.id, "name": s.name, "project_name": project_map.get(s.project_id, s.project_id)}
                for s in sections
            ]
        except Exception as e:
            logger.error("list_todoist_sections failed: %s", e)
            raise ModelRetry(str(e))

    @agent.tool_plain
    def list_todoist_tasks(project_name: Optional[str] = None) -> list[dict]:
        """List active (uncompleted) Todoist tasks. Optionally filter by project name.
        Returns task IDs which are required for update, close, and delete operations."""
        logger.info("Tool called: list_todoist_tasks | project=%s", project_name)
        try:
            kwargs = {}
            if project_name:
                kwargs["project_id"] = _resolve_project(project_name)
            tasks = _get_all_tasks(**kwargs)
            project_map = _build_project_map()
            section_map = _build_section_map()
            return [_task_to_dict(t, project_map, section_map) for t in tasks]
        except Exception as e:
            logger.error("list_todoist_tasks failed: %s", e)
            raise ModelRetry(str(e))

    @agent.tool_plain
    def create_todoist_task(
        content: str,
        project_name: str = "Inbox",
        description: str = "",
        section_name: Optional[str] = None,
        due_date: Optional[str] = None,
    ) -> dict:
        """Create a new Todoist task.

        due_date must be in YYYY-MM-DD format (date only, no time).
        project_name defaults to 'Inbox' if not specified.
        """
        logger.info("Tool called: create_todoist_task → %s | project=%s", content, project_name)
        try:
            project_id = _resolve_project(project_name)
            kwargs = {
                "content": content,
                "project_id": project_id,
                "description": description,
            }
            if section_name:
                kwargs["section_id"] = _resolve_section(section_name, project_id)
            if due_date:
                kwargs["due_date"] = date.fromisoformat(due_date)
            task = api.add_task(**kwargs)
            project_map = _build_project_map()
            section_map = _build_section_map(project_id)
            date_str = f", due {_friendly_date(date.fromisoformat(due_date))}" if due_date else ""
            fire(f"✅ Task created: {content} (in {project_name}{date_str})")
            return _task_to_dict(task, project_map, section_map)
        except Exception as e:
            logger.error("create_todoist_task failed: %s", e)
            raise ModelRetry(str(e))

    @agent.tool_plain
    def update_todoist_task(
        task_id: str,
        content: Optional[str] = None,
        description: Optional[str] = None,
        due_date: Optional[str] = None,
    ) -> dict:
        """Update an existing Todoist task. Only provided fields are changed.
        Pass due_date="" to remove the due date. due_date must be YYYY-MM-DD format."""
        logger.info("Tool called: update_todoist_task → %s", task_id)
        try:
            kwargs = {}
            if content is not None:
                kwargs["content"] = content
            if description is not None:
                kwargs["description"] = description
            if due_date is not None:
                if due_date == "":
                    kwargs["due_string"] = "no date"
                else:
                    kwargs["due_date"] = date.fromisoformat(due_date)
            task = api.update_task(task_id, **kwargs)
            project_map = _build_project_map()
            section_map = _build_section_map()
            date_str = f", due {_friendly_date(task.due.date)}" if task.due else ""
            fire(f"✏️ Task updated: {task.content}{date_str}")
            return _task_to_dict(task, project_map, section_map)
        except Exception as e:
            logger.error("update_todoist_task failed: %s", e)
            raise ModelRetry(str(e))

    @agent.tool_plain
    def close_todoist_task(task_id: str) -> str:
        """Mark a Todoist task as completed."""
        logger.info("Tool called: close_todoist_task → %s", task_id)
        try:
            task = api.get_task(task_id)
            api.complete_task(task_id)
            fire(f"✅ Task completed: {task.content}")
            return f"Task '{task.content}' completed."
        except Exception as e:
            logger.error("close_todoist_task failed: %s", e)
            raise ModelRetry(str(e))

    @agent.tool_plain
    def delete_todoist_task(task_id: str) -> str:
        """Permanently delete a Todoist task. Use close_todoist_task to complete a task instead."""
        logger.info("Tool called: delete_todoist_task → %s", task_id)
        try:
            task = api.get_task(task_id)
            api.delete_task(task_id)
            fire(f"🗑 Task deleted: {task.content}")
            return f"Task '{task.content}' deleted."
        except Exception as e:
            logger.error("delete_todoist_task failed: %s", e)
            raise ModelRetry(str(e))
