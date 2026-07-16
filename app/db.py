"""Thin sqlite3 data layer. No ORM: the schema is small and stable, and
plain sqlite3 keeps the dependency list (and thing-to-debug list) short."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(class_id, name)
);

CREATE TABLE IF NOT EXISTS essays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_filename TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pronunciation_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    find_text TEXT NOT NULL,
    replace_text TEXT NOT NULL,
    whole_word INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    essay_id INTEGER NOT NULL REFERENCES essays(id) ON DELETE CASCADE,
    engine TEXT NOT NULL,
    voice_id TEXT NOT NULL,
    rate INTEGER NOT NULL,
    pitch INTEGER NOT NULL,
    volume INTEGER NOT NULL,
    audio_path TEXT NOT NULL,
    audio_format TEXT NOT NULL,
    timings_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_id);
CREATE INDEX IF NOT EXISTS idx_essays_student ON essays(student_id);
CREATE INDEX IF NOT EXISTS idx_exports_essay ON audio_exports(essay_id);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list:
    return [dict(r) for r in rows]


# --- classes -----------------------------------------------------------

def create_class(name: str) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO classes (name, created_at) VALUES (?, ?)",
            (name.strip(), time.time()),
        )
        row = conn.execute("SELECT * FROM classes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return row_to_dict(row)


def get_class(class_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        return row_to_dict(row)


def list_classes() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM classes ORDER BY name COLLATE NOCASE").fetchall()
        return rows_to_list(rows)


def delete_class(class_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM classes WHERE id = ?", (class_id,))


# --- students ------------------------------------------------------------

def create_student(class_id: int, name: str) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO students (class_id, name, created_at) VALUES (?, ?, ?)",
            (class_id, name.strip(), time.time()),
        )
        row = conn.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
        return row_to_dict(row)


def get_student(student_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        return row_to_dict(row)


def get_or_create_student(class_id: int, name: str) -> dict:
    name = name.strip()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM students WHERE class_id = ? AND name = ?", (class_id, name)
        ).fetchone()
        if row:
            return dict(row)
    return create_student(class_id, name)


def list_students(class_id: Optional[int] = None) -> list:
    with get_conn() as conn:
        if class_id is not None:
            rows = conn.execute(
                "SELECT * FROM students WHERE class_id = ? ORDER BY name COLLATE NOCASE",
                (class_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM students ORDER BY name COLLATE NOCASE").fetchall()
        return rows_to_list(rows)


def delete_student(student_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM students WHERE id = ?", (student_id,))


# --- essays --------------------------------------------------------------

def create_essay(student_id: int, title: str, content: str, source_filename: Optional[str] = None) -> dict:
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO essays (student_id, title, content, source_filename, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (student_id, title.strip(), content, source_filename, now, now),
        )
        essay_id = cur.lastrowid
    return get_essay(essay_id)


def get_essay(essay_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT essays.*, students.name AS student_name, students.class_id AS class_id,
                      classes.name AS class_name
               FROM essays
               JOIN students ON students.id = essays.student_id
               JOIN classes ON classes.id = students.class_id
               WHERE essays.id = ?""",
            (essay_id,),
        ).fetchone()
        return row_to_dict(row)


def update_essay(essay_id: int, title: Optional[str] = None, content: Optional[str] = None) -> Optional[dict]:
    fields = []
    values = []
    if title is not None:
        fields.append("title = ?")
        values.append(title.strip())
    if content is not None:
        fields.append("content = ?")
        values.append(content)
    if not fields:
        return get_essay(essay_id)
    fields.append("updated_at = ?")
    values.append(time.time())
    values.append(essay_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE essays SET {', '.join(fields)} WHERE id = ?", values)
    return get_essay(essay_id)


def delete_essay(essay_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM essays WHERE id = ?", (essay_id,))


def list_essays(class_id: Optional[int] = None, student_id: Optional[int] = None, query: Optional[str] = None) -> list:
    sql = """SELECT essays.*, students.name AS student_name, students.class_id AS class_id,
                    classes.name AS class_name
             FROM essays
             JOIN students ON students.id = essays.student_id
             JOIN classes ON classes.id = students.class_id
             WHERE 1=1"""
    params: list = []
    if class_id is not None:
        sql += " AND students.class_id = ?"
        params.append(class_id)
    if student_id is not None:
        sql += " AND essays.student_id = ?"
        params.append(student_id)
    if query:
        sql += " AND (essays.title LIKE ? OR essays.content LIKE ? OR students.name LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like, like])
    sql += " ORDER BY essays.updated_at DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return rows_to_list(rows)


# --- pronunciation rules ---------------------------------------------------

def create_rule(find_text: str, replace_text: str, whole_word: bool = True) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pronunciation_rules (find_text, replace_text, whole_word, created_at) VALUES (?, ?, ?, ?)",
            (find_text.strip(), replace_text.strip(), int(whole_word), time.time()),
        )
        row = conn.execute("SELECT * FROM pronunciation_rules WHERE id = ?", (cur.lastrowid,)).fetchone()
        return row_to_dict(row)


def list_rules() -> list:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM pronunciation_rules ORDER BY find_text COLLATE NOCASE").fetchall()
        return rows_to_list(rows)


def delete_rule(rule_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM pronunciation_rules WHERE id = ?", (rule_id,))


# --- audio exports ---------------------------------------------------------

def create_audio_export(
    essay_id: int,
    engine: str,
    voice_id: str,
    rate: int,
    pitch: int,
    volume: int,
    audio_path: str,
    audio_format: str,
    timings_json: str,
) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO audio_exports
               (essay_id, engine, voice_id, rate, pitch, volume, audio_path, audio_format, timings_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (essay_id, engine, voice_id, rate, pitch, volume, audio_path, audio_format, timings_json, time.time()),
        )
        row = conn.execute("SELECT * FROM audio_exports WHERE id = ?", (cur.lastrowid,)).fetchone()
        return row_to_dict(row)


def latest_audio_export(essay_id: int, engine: str, voice_id: str, rate: int, pitch: int, volume: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM audio_exports
               WHERE essay_id = ? AND engine = ? AND voice_id = ? AND rate = ? AND pitch = ? AND volume = ?
               ORDER BY created_at DESC LIMIT 1""",
            (essay_id, engine, voice_id, rate, pitch, volume),
        ).fetchone()
        return row_to_dict(row)
