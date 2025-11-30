from fastapi import FastAPI, HTTPException
from typing import Any, Dict
from pydantic import BaseModel

from mcp.swimbuddz_core_mcp import tools

app = FastAPI(title="SwimBuddz MCP Server")


class ToolCallRequest(BaseModel):
    token: str
    arguments: Dict[str, Any] = {}


@app.get("/tools")
async def list_tools():
    """List available tools."""
    return [
        {
            "name": "get_current_member_profile",
            "description": "Get the profile of the currently authenticated member.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "list_upcoming_sessions",
            "description": "List all upcoming sessions.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "get_session_details",
            "description": "Get details of a specific session.",
            "parameters": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        },
        {
            "name": "sign_in_to_session",
            "description": "Sign in to a session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "needs_ride": {"type": "boolean"},
                    "can_offer_ride": {"type": "boolean"},
                    "ride_notes": {"type": "string"},
                },
                "required": ["session_id"],
            },
        },
        {
            "name": "get_my_attendance_history",
            "description": "Get the attendance history of the current member.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "list_announcements",
            "description": "List all announcements.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "create_announcement",
            "description": "Create a new announcement (Admin only).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "category": {"type": "string"},
                    "is_pinned": {"type": "boolean"},
                },
                "required": ["title", "body", "category"],
            },
        },
    ]


@app.post("/tools/{tool_name}/call")
async def call_tool(tool_name: str, request: ToolCallRequest):
    """Call a specific tool."""
    if not hasattr(tools, tool_name):
        raise HTTPException(status_code=404, detail=f"Tool {tool_name} not found")

    tool_func = getattr(tools, tool_name)

    try:
        # Pass token as first argument, then unpack arguments
        result = await tool_func(request.token, **request.arguments)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
