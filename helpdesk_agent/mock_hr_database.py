"""
Mock HR system of record + the access-control rule (Lab 4).

Each agent run gets its OWN in-memory copy of data/hr_database.json, so tickets and
leave requests created by one test never leak into another test. That isolation is
what makes the side-effect assertions in Labs 3 and 5 reliable ("exactly one leave
request exists for 2026-11-24").

Access-control rule (enforced only when AgentSettings.enforce_access_control=True):
    A caller may read or act on an employee record if they ARE that employee,
    are that employee's DIRECT MANAGER (read-only), or have the "hr" role (read-only).

Run `python -m helpdesk_agent.mock_hr_database` for an offline self-check.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HR_DATABASE_PATH = PROJECT_ROOT / "data" / "hr_database.json"

AccessKind = Literal["read", "write"]


class AccessControlError(PermissionError):
    """Raised when the logged-in caller is not allowed to touch a record."""


class RecordNotFoundError(LookupError):
    """Raised for an unknown employee or leave-request id."""


@lru_cache(maxsize=1)
def _load_seed_data() -> dict[str, Any]:
    """Read the JSON seed file once per process."""
    return json.loads(HR_DATABASE_PATH.read_text(encoding="utf-8"))


@dataclass
class HRDatabase:
    """An isolated, mutable copy of the HR data for a single agent conversation."""

    caller_employee_id: str
    enforce_access_control: bool
    employees: dict[str, dict[str, Any]] = field(default_factory=dict)
    it_tickets: list[dict[str, Any]] = field(default_factory=list)
    leave_requests: list[dict[str, Any]] = field(default_factory=list)
    today: str = ""
    initial_ticket_count: int = 0  # tickets that existed before this conversation

    @classmethod
    def for_caller(cls, caller_employee_id: str, enforce_access_control: bool) -> "HRDatabase":
        seed = _load_seed_data()
        if caller_employee_id not in seed["employees"]:
            raise RecordNotFoundError(f"Unknown logged-in employee id: {caller_employee_id}")
        return cls(
            caller_employee_id=caller_employee_id,
            enforce_access_control=enforce_access_control,
            employees=copy.deepcopy(seed["employees"]),
            it_tickets=copy.deepcopy(seed["existing_it_tickets"]),
            today=seed["today"],
            initial_ticket_count=len(seed["existing_it_tickets"]),
        )

    @property
    def tickets_created_this_conversation(self) -> list[dict[str, Any]]:
        return self.it_tickets[self.initial_ticket_count :]

    # ---- identity helpers -----------------------------------------------------------
    @property
    def caller(self) -> dict[str, Any]:
        return self.employees[self.caller_employee_id]

    def get_employee(self, employee_id: str) -> dict[str, Any]:
        employee = self.employees.get(employee_id)
        if employee is None:
            raise RecordNotFoundError(f"No employee with id {employee_id}")
        return employee

    def direct_report_ids(self, manager_id: str) -> list[str]:
        return [eid for eid, emp in self.employees.items() if emp.get("manager_id") == manager_id]

    # ---- the access-control rule (Lab 4) -----------------------------------------------
    def is_access_allowed(self, target_employee_id: str, access: AccessKind) -> bool:
        """Pure policy check — no side effects, easy to unit test."""
        caller_id = self.caller_employee_id
        if target_employee_id == caller_id:
            return True
        if access == "write":
            return False  # nobody may act on someone else's behalf
        target = self.get_employee(target_employee_id)
        is_direct_manager = target.get("manager_id") == caller_id
        is_hr_staff = self.caller.get("role") == "hr"
        return is_direct_manager or is_hr_staff

    def require_access(self, target_employee_id: str, access: AccessKind) -> None:
        """Raise AccessControlError unless allowed.

        When enforcement is OFF (the planted Lab 4 bug) this is a no-op, so the
        agent will happily read or write any record the LLM asks for.
        """
        self.get_employee(target_employee_id)  # unknown ids still fail
        if not self.enforce_access_control:
            return
        if not self.is_access_allowed(target_employee_id, access):
            raise AccessControlError(
                f"Employee {self.caller_employee_id} is not authorised to {access} "
                f"records of employee {target_employee_id}."
            )


# ------------------------------------------------------------------------------
# Offline self-check: `python -m helpdesk_agent.mock_hr_database`
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    secure = HRDatabase.for_caller("E101", enforce_access_control=True)
    assert secure.is_access_allowed("E101", "read"), "self read must be allowed"
    assert not secure.is_access_allowed("E102", "read"), "peer read must be denied"
    manager = HRDatabase.for_caller("E201", enforce_access_control=True)
    assert manager.is_access_allowed("E102", "read"), "manager reads direct report"
    assert not manager.is_access_allowed("E103", "read"), "manager cannot read other team"
    assert not manager.is_access_allowed("E102", "write"), "manager cannot act for report"
    hr = HRDatabase.for_caller("E105", enforce_access_control=True)
    assert hr.is_access_allowed("E104", "read"), "HR can read anyone"
    try:
        secure.require_access("E102", "read")
        raise SystemExit("FAIL: expected AccessControlError")
    except AccessControlError:
        pass
    insecure = HRDatabase.for_caller("E101", enforce_access_control=False)
    insecure.require_access("E102", "read")  # the planted bug: no error
    print("mock_hr_database self-check: all access-control rules behave as expected.")
