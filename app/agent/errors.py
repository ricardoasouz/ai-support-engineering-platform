"""Typed failures at the server-controlled agent boundary."""


class AgentError(RuntimeError):
    """Base controlled-agent error."""


class AgentValidationError(AgentError):
    """Planner output or final output violated a local guardrail."""


class AgentBudgetExceededError(AgentError):
    """Execution reached a configured step, call, repeat, or time limit."""


class UnknownToolError(AgentValidationError):
    """The planner selected a tool outside the registry allowlist."""


class UnauthorizedToolArgumentsError(AgentValidationError):
    """A tool attempted to access an incident outside the execution scope."""


class ToolExecutionError(AgentError):
    """An approved tool could not produce a valid result."""


class ToolTimeoutError(ToolExecutionError):
    """An approved read-only tool exceeded its configured duration."""
