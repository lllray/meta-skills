# -*- coding: utf-8 -*-
"""
meta-skills - SQLite 数据层
记录已安装技能、优先级等；打分与使用次数以本地 JSON + meta-skills-rank-lists 为准。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_DB_NAME = "meta_skills.db"


@dataclass
class SkillRecord:
    id: Optional[int]
    name: str
    source_url: str
    installed_at: float
    priority: int
    local_modified: bool
    version_tag: str = ""

    @classmethod
    def from_row(cls, row: tuple) -> "SkillRecord":
        return cls(
            id=row[0],
            name=row[1],
            source_url=row[2],
            installed_at=row[3],
            priority=row[4],
            local_modified=bool(row[5]),
            version_tag=row[6] or "",
        )


@dataclass
class SkillScore:
    skill_name: str
    success_ratio: float
    feedback_ratio: float
    composite_score: float
    total_invocations: int
    total_feedback: int


class DBHandler:
    def __init__(self, db_path: str | Path | None = None, base_dir: str | Path | None = None):
        if db_path is None and base_dir is None:
            base_dir = Path(__file__).resolve().parent
        if db_path is None:
            db_path = Path(base_dir) / DEFAULT_DB_NAME
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        c = self._conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                source_url TEXT NOT NULL,
                installed_at REAL NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                local_modified INTEGER NOT NULL DEFAULT 0,
                version_tag TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id INTEGER NOT NULL,
                success INTEGER NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (skill_id) REFERENCES skills(id)
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id INTEGER NOT NULL,
                rating REAL NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (skill_id) REFERENCES skills(id)
            );
            CREATE INDEX IF NOT EXISTS idx_invocations_skill ON invocations(skill_id);
            CREATE INDEX IF NOT EXISTS idx_feedback_skill ON feedback(skill_id);
        """)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def register_skill(
        self,
        name: str,
        source_url: str,
        priority: int = 0,
        local_modified: bool = False,
        version_tag: str = "",
    ) -> int:
        conn = self._get_conn()
        now = time.time()
        cur = conn.execute(
            """
            INSERT INTO skills (name, source_url, installed_at, priority, local_modified, version_tag)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                source_url = excluded.source_url,
                installed_at = excluded.installed_at,
                priority = excluded.priority,
                local_modified = excluded.local_modified,
                version_tag = excluded.version_tag
            """,
            (name, source_url, now, priority, 1 if local_modified else 0, version_tag),
        )
        conn.commit()
        return cur.lastrowid or self.get_skill_id_by_name(name)

    def get_skill_id_by_name(self, name: str) -> int:
        row = self._get_conn().execute("SELECT id FROM skills WHERE name = ?", (name,)).fetchone()
        if not row:
            raise KeyError(f"Skill not found: {name}")
        return row["id"]

    def get_skill(self, name: str) -> Optional[SkillRecord]:
        row = self._get_conn().execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        return SkillRecord.from_row(tuple(row))

    def list_skills(self) -> list[SkillRecord]:
        rows = self._get_conn().execute(
            "SELECT * FROM skills ORDER BY priority DESC, installed_at DESC"
        ).fetchall()
        return [SkillRecord.from_row(tuple(r)) for r in rows]

    def set_priority(self, name: str, priority: int) -> None:
        conn = self._get_conn()
        sid = self.get_skill_id_by_name(name)
        conn.execute("UPDATE skills SET priority = ? WHERE id = ?", (priority, sid))
        conn.commit()

    def set_local_modified(self, name: str, modified: bool) -> None:
        conn = self._get_conn()
        sid = self.get_skill_id_by_name(name)
        conn.execute("UPDATE skills SET local_modified = ? WHERE id = ?", (1 if modified else 0, sid))
        conn.commit()

    def record_invocation(self, skill_name: str, success: bool) -> None:
        conn = self._get_conn()
        sid = self.get_skill_id_by_name(skill_name)
        conn.execute(
            "INSERT INTO invocations (skill_id, success, created_at) VALUES (?, ?, ?)",
            (sid, 1 if success else 0, time.time()),
        )
        conn.commit()

    def record_feedback(self, skill_name: str, rating: float) -> None:
        if rating < 0 or rating > 5:
            rating = max(1.0, min(5.0, rating))
        conn = self._get_conn()
        sid = self.get_skill_id_by_name(skill_name)
        conn.execute(
            "INSERT INTO feedback (skill_id, rating, created_at) VALUES (?, ?, ?)",
            (sid, rating, time.time()),
        )
        conn.commit()

    def get_score(
        self,
        skill_name: str,
        success_weight: float = 0.4,
        feedback_weight: float = 0.6,
        min_invocations: int = 3,
    ) -> Optional[SkillScore]:
        try:
            sid = self.get_skill_id_by_name(skill_name)
        except KeyError:
            return None
        conn = self._get_conn()
        inv = conn.execute(
            "SELECT COUNT(*), SUM(success) FROM invocations WHERE skill_id = ?", (sid,)
        ).fetchone()
        total_inv = inv[0] or 0
        success_count = inv[1] or 0
        fb = conn.execute(
            "SELECT COUNT(*), AVG(rating) FROM feedback WHERE skill_id = ?", (sid,)
        ).fetchone()
        total_fb = fb[0] or 0
        avg_rating = fb[1] or 0.0
        if total_inv < min_invocations and total_fb == 0:
            return None
        success_ratio = success_count / total_inv if total_inv else 0.0
        feedback_ratio = (avg_rating - 1.0) / 4.0 if total_fb else 0.5
        feedback_ratio = max(0.0, min(1.0, feedback_ratio))
        composite = success_ratio * success_weight + feedback_ratio * feedback_weight
        composite = round(composite * 5.0, 2)
        return SkillScore(
            skill_name=skill_name,
            success_ratio=success_ratio,
            feedback_ratio=feedback_ratio,
            composite_score=composite,
            total_invocations=total_inv,
            total_feedback=total_fb,
        )

    def get_all_scores(
        self,
        success_weight: float = 0.4,
        feedback_weight: float = 0.6,
        min_invocations: int = 3,
    ) -> list[SkillScore]:
        result = []
        for rec in self.list_skills():
            s = self.get_score(
                rec.name,
                success_weight=success_weight,
                feedback_weight=feedback_weight,
                min_invocations=min_invocations,
            )
            if s is not None:
                result.append(s)
        result.sort(key=lambda x: x.composite_score, reverse=True)
        return result
