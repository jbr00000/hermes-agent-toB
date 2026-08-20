"""Scratch: dump tool events for specific runs."""
import json
import os
import sys

HERMES_HOME = r"F:\project\Hermes Agent\hermes-agent\.hermes-dev"
os.environ["HERMES_HOME"] = HERMES_HOME
sys.path.insert(0, r"F:\project\Hermes Agent\hermes-agent")

from dotenv import load_dotenv

load_dotenv(os.path.join(HERMES_HOME, ".env"))

from server.storage.database import session_scope
from server.storage.models import ToolEvent
from sqlalchemy import select

RUN_PREFIXES = sys.argv[1:] or ["0ccb8d23", "4bf28e7b", "60d50de6"]

with session_scope() as session:
    rows = session.scalars(select(ToolEvent).order_by(ToolEvent.id)).all()
    for e in rows:
        if not any(e.run_id and e.run_id.startswith(p) for p in RUN_PREFIXES):
            continue
        payload = e.payload or {}
        summary = json.dumps(payload, ensure_ascii=False)[:300]
        print(f"[{e.run_id[:8]}] {e.event_type} tool={e.tool_name} status={e.status}")
        print(f"    {summary}")
