"""
Tool definitions (what the LLM sees) and tool implementations (what actually runs).

Two schema styles exist for Lab 3:

  VAGUE   (planted bug) — tool docs that drifted from the backend: create_it_ticket
          still describes its old "asset inventory" purpose, a catch-all
          `escalate_to_hr` swallows every reported problem, priority is documented
          as P1–P4 while the backend wants low/medium/high/critical, and dates are
          documented as "next Monday". Symptoms the eval catches: laptop issues
          routed to HR, priority "P2", dates like "next Monday", leave type "PTO".

  PRECISE (fix)         — explicit when-to-use / when-NOT-to-use guidance, enums,
          ISO dates, all fields required, and `strict: true` so the API guarantees
          the arguments match the schema.

Every call is recorded as a `ToolCallRecord` and returned in the agent output, so
promptfoo assertions can check *behaviour* (which tool, which arguments, was access
denied?) rather than just the final text.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable

from helpdesk_agent.mock_hr_database import AccessControlError, HRDatabase, RecordNotFoundError
from helpdesk_agent.settings import ToolSchemaStyle

IT_TICKET_CATEGORIES = ["hardware", "software", "access", "network", "security"]
IT_TICKET_PRIORITIES = ["low", "medium", "high", "critical"]
LEAVE_TYPES = ["annual", "sick", "parental", "bereavement", "unpaid"]


# =============================================================================
# 1. Tool schemas shown to the model
# =============================================================================

def _vague_tool_schemas() -> list[dict[str, Any]]:
    """PLANTED BUG (Lab 3): tool docs that have drifted away from the real backend.

    Every description below is the kind of thing that sits in a prototype for months:
      * create_it_ticket still describes its OLD purpose (asset inventory), so the
        model sends real problems to the catch-all escalate_to_hr instead.
      * priority is documented as "P1–P4" (the old helpdesk convention), but the
        ticketing backend only accepts low / medium / high / critical.
      * leave dates are documented with natural-language examples ("next Monday"),
        but the HR system needs YYYY-MM-DD.
      * leave_type examples ("vacation", "PTO") don't match the HR system's types.
    Nothing here looks obviously broken in a code review — only an eval catches it.
    """
    return [
        {
            "name": "get_leave_balance",
            "description": "Gets leave info.",
            "input_schema": {
                "type": "object",
                "properties": {"employee_id": {"type": "string"}},
            },
        },
        {
            "name": "get_employee_profile",
            "description": "Gets employee info.",
            "input_schema": {
                "type": "object",
                "properties": {"employee_id": {"type": "string"}},
            },
        },
        {
            "name": "create_it_ticket",
            "description": (
                "Create a record in the IT asset system. Used by IT Operations to log "
                "asset inventory changes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Asset type."},
                    "priority": {
                        "type": "string",
                        "description": "Ticket priority from P1 (most urgent) to P4 (least urgent).",
                    },
                    "summary": {"type": "string"},
                },
            },
        },
        {
            "name": "submit_leave_request",
            "description": "Submit leave.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "employee_id": {"type": "string"},
                    "start_date": {
                        "type": "string",
                        "description": "When the leave starts, e.g. 'next Monday' or 'Dec 3'.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "When the leave ends, e.g. 'next Monday' or 'Dec 5'.",
                    },
                    "leave_type": {
                        "type": "string",
                        "description": "Type of leave, e.g. 'vacation', 'PTO' or 'sick day'.",
                    },
                },
            },
        },
        {
            "name": "cancel_leave_request",
            "description": "Cancel leave.",
            "input_schema": {
                "type": "object",
                "properties": {"request_id": {"type": "string"}},
            },
        },
        {
            "name": "escalate_to_hr",
            # Combined with the stale create_it_ticket description above, this
            # catch-all description pulls every reported problem to HR.
            "description": (
                "Route the employee's issue to the support team. Use this for any problem "
                "or request the employee reports: devices, equipment, accounts, access, "
                "software, security concerns, payroll or HR matters."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "summary": {"type": "string"},
                },
            },
        },
    ]


def _precise_tool_schemas() -> list[dict[str, Any]]:
    """FIX (Lab 3): explicit guidance, enums, required fields, strict validation."""
    employee_id_property = {
        "type": "string",
        "description": (
            "Employee id in the form 'E' + 3 digits, e.g. 'E101'. Use the logged-in "
            "employee's own id unless they explicitly ask about someone else."
        ),
    }
    return [
        {
            "name": "get_leave_balance",
            "description": (
                "Look up the remaining annual leave days, sick leave days and days carried "
                "forward from last year for ONE employee. Use for any question about how "
                "much leave someone has left."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"employee_id": employee_id_property},
                "required": ["employee_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_employee_profile",
            "description": (
                "Look up an employee's HR profile (name, title, department, manager, work "
                "email and personal contact details). Only use when the employee asks about "
                "their own profile or a manager asks about a direct report."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"employee_id": employee_id_property},
                "required": ["employee_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "create_it_ticket",
            "description": (
                "Open an IT support ticket for the logged-in employee. Use for ANY technology "
                "problem or request: broken, damaged, lost or stolen devices; equipment "
                "requests (monitor, keyboard, headset); software installs; account or system "
                "access; network/VPN issues; phishing and security incidents. "
                "Never use escalate_to_hr for these. "
                "Priority guide — critical: security incident, phishing click, lost/stolen "
                "device; high: employee cannot work or has a deadline within 24 hours; "
                "medium: work is degraded but possible; low: requests and nice-to-haves."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": IT_TICKET_CATEGORIES},
                    "priority": {"type": "string", "enum": IT_TICKET_PRIORITIES},
                    "summary": {
                        "type": "string",
                        "description": "One sentence describing the problem in the employee's words.",
                    },
                },
                "required": ["category", "priority", "summary"],
                "additionalProperties": False,
            },
        },
        {
            "name": "submit_leave_request",
            "description": (
                "Submit a leave request for the logged-in employee. Dates must be ISO "
                "format YYYY-MM-DD; resolve relative dates ('next Monday') using today's "
                "date from the system prompt. For a single day, start_date == end_date."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "employee_id": employee_id_property,
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "leave_type": {"type": "string", "enum": LEAVE_TYPES},
                },
                "required": ["employee_id", "start_date", "end_date", "leave_type"],
                "additionalProperties": False,
            },
        },
        {
            "name": "cancel_leave_request",
            "description": (
                "Cancel a leave request created earlier in this conversation, using the "
                "request_id returned by submit_leave_request (e.g. 'LR-1'). Use this when "
                "the employee changes their mind or changes the dates — cancel the old "
                "request, then submit a new one."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"request_id": {"type": "string"}},
                "required": ["request_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "escalate_to_hr",
            "description": (
                "Hand the conversation to a human HR Business Partner. ONLY for people "
                "issues that policies cannot resolve: harassment or misconduct reports, "
                "payroll disputes, sensitive personal circumstances (illness, bereavement, "
                "family emergencies), or when the employee explicitly asks for a human. "
                "Never use for IT or equipment problems — use create_it_ticket instead."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": [
                            "misconduct_report",
                            "payroll_dispute",
                            "sensitive_personal_matter",
                            "employee_requested_human",
                        ],
                    },
                    "summary": {"type": "string"},
                },
                "required": ["reason", "summary"],
                "additionalProperties": False,
            },
        },
    ]


def get_tool_schemas(style: ToolSchemaStyle) -> list[dict[str, Any]]:
    """Return the tool list for the requested style ('vague' = bug, 'precise' = fix)."""
    return _vague_tool_schemas() if style == "vague" else _precise_tool_schemas()


# =============================================================================
# 2. Tool implementations
# =============================================================================

@dataclass
class ToolCallRecord:
    """What the eval sees for every tool call the model made."""

    name: str
    input: dict[str, Any]
    status: str                       # "ok" | "denied" | "error"
    target_employee_id: str | None    # whose record was touched (for security asserts)
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HelpdeskToolExecutor:
    """Runs tool calls against one conversation's isolated HRDatabase."""

    def __init__(self, database: HRDatabase) -> None:
        self.database = database
        self.call_log: list[ToolCallRecord] = []
        self._handlers: dict[str, Callable[[dict[str, Any]], tuple[dict[str, Any], str | None]]] = {
            "get_leave_balance": self._get_leave_balance,
            "get_employee_profile": self._get_employee_profile,
            "create_it_ticket": self._create_it_ticket,
            "submit_leave_request": self._submit_leave_request,
            "cancel_leave_request": self._cancel_leave_request,
            "escalate_to_hr": self._escalate_to_hr,
        }

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        """Execute one call. Returns (JSON text for the model, is_error flag)."""
        handler = self._handlers.get(tool_name)
        target_id = tool_input.get("employee_id") if isinstance(tool_input, dict) else None
        try:
            if handler is None:
                raise RecordNotFoundError(f"Unknown tool {tool_name}")
            result, target_id = handler(tool_input)
            status = "ok"
        except AccessControlError as exc:
            result, status = {"error": "access_denied", "message": str(exc)}, "denied"
        except (RecordNotFoundError, KeyError, ValueError, TypeError) as exc:
            result, status = {"error": "invalid_request", "message": str(exc)}, "error"

        self.call_log.append(
            ToolCallRecord(
                name=tool_name,
                input=dict(tool_input) if isinstance(tool_input, dict) else {"raw": tool_input},
                status=status,
                target_employee_id=target_id,
                result=result,
            )
        )
        return json.dumps(result), status != "ok"

    # ---- individual tools ---------------------------------------------------------------
    # Each handler returns (result_dict, id_of_employee_whose_record_was_touched).

    def _get_leave_balance(self, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
        employee_id = args["employee_id"]
        self.database.require_access(employee_id, "read")
        employee = self.database.get_employee(employee_id)
        return {"employee_id": employee_id, "name": employee["name"], **employee["leave_balance"]}, employee_id

    def _get_employee_profile(self, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
        employee_id = args["employee_id"]
        self.database.require_access(employee_id, "read")
        employee = self.database.get_employee(employee_id)
        profile = {k: v for k, v in employee.items() if k != "leave_balance"}
        return {"employee_id": employee_id, **profile}, employee_id

    def _create_it_ticket(self, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
        # The vague schema lets the model send anything; we store it verbatim so the
        # eval can see exactly what a downstream ticketing system would have received.
        ticket = {
            "ticket_id": f"IT-{5000 + len(self.database.it_tickets)}",
            "employee_id": self.database.caller_employee_id,
            "category": args.get("category"),
            "priority": args.get("priority"),
            "summary": args.get("summary"),
            "status": "open",
        }
        self.database.it_tickets.append(ticket)
        return ticket, self.database.caller_employee_id

    def _submit_leave_request(self, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
        employee_id = args.get("employee_id") or self.database.caller_employee_id
        self.database.require_access(employee_id, "write")
        request = {
            "request_id": f"LR-{len(self.database.leave_requests) + 1}",
            "employee_id": employee_id,
            "start_date": args.get("start_date"),
            "end_date": args.get("end_date"),
            "leave_type": args.get("leave_type"),
            "status": "pending_manager_approval",
        }
        self.database.leave_requests.append(request)
        return request, employee_id

    def _cancel_leave_request(self, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
        request_id = args["request_id"]
        for request in self.database.leave_requests:
            if request["request_id"] == request_id:
                self.database.require_access(request["employee_id"], "write")
                request["status"] = "cancelled"
                return request, request["employee_id"]
        raise RecordNotFoundError(f"No leave request with id {request_id}")

    def _escalate_to_hr(self, args: dict[str, Any]) -> tuple[dict[str, Any], str]:
        return {
            "escalation_id": "HR-ESC-1",
            "assigned_to": "HR Business Partner on duty",
            "reason": args.get("reason"),
            "summary": args.get("summary"),
        }, self.database.caller_employee_id
