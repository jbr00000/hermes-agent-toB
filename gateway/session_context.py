"""DEPRECATED — session_context moved to agent/session_context.py (to-B fork).

Shim keeps remaining gateway-internal callers working until gateway/ is deleted
(Step 2.1). New code MUST import from agent.session_context.
"""
from agent import session_context as _m
globals().update({k: v for k, v in vars(_m).items() if not k.startswith('__')})
