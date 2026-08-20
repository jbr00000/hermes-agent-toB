"""Scratch: inspect recent task runs + tool events for failure reasons."""
import os
import sys

HERMES_HOME = r"F:\project\Hermes Agent\hermes-agent\.hermes-dev"
os.environ["HERMES_HOME"] = HERMES_HOME
sys.path.insert(0, r"F:\project\Hermes Agent\hermes-agent")

from dotenv import load_dotenv

load_dotenv(os.path.join(HERMES_HOME, ".env"))

from server.storage import get_repository

repo = get_repository()

# 最近几个任务及其运行状态/错误
from server.storage.database import session_scope
from server.storage.models import AgentTask, TaskRun
from sqlalchemy import select

with session_scope() as session:
    tasks = session.scalars(select(AgentTask).order_by(AgentTask.updated_at.desc()).limit(5)).all()
    for t in tasks:
        print(f"TASK {t.id[:8]} status={t.status} title={t.title!r} current_run={t.current_run_id}")
        runs = session.scalars(
            select(TaskRun).where(TaskRun.task_id == t.id).order_by(TaskRun.started_at)
        ).all()
        for r in runs:
            print(f"  RUN {r.id[:8]} phase={r.phase} status={r.status} attempt={r.attempt}")
            err = getattr(r, "error", None)
            if err:
                print(f"    ERROR: {str(err)[:600]}")
            if r.completed_at and r.started_at:
                print(f"    duration: {r.completed_at - r.started_at:.1f}s")
