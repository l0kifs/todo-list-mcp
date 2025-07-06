from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base, MCPMeta
import os

project_root = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("TODO_MCP_DB_PATH", os.path.join(project_root, "todo_mcp.sqlite3"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_or_create_meta():
    with SessionLocal() as db:
        meta = db.query(MCPMeta).filter_by(id=1).first()
        if not meta:
            meta = MCPMeta(id=1, worker_pid=None)
            db.add(meta)
            db.commit()
            db.refresh(meta)
        return meta

def set_worker_pid(pid: int):
    with SessionLocal() as db:
        meta = db.query(MCPMeta).filter_by(id=1).first()
        if not meta:
            meta = MCPMeta(id=1, worker_pid=pid)
            db.add(meta)
        else:
            meta.worker_pid = pid
        db.commit() 