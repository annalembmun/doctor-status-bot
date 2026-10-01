"""
db.py — слой данных бота «Статус врача».

Идея: всё, что видно в утренней сводке, приводим к нормальной реляционной схеме.
Бот потом отвечает по базе, а не по захардкоженному тексту.

Таблицы:
  doctors          — врачи (ФИО, последний вход)
  courses          — курсы (название, сколько всего назначено)
  enrollments      — кто на каком курсе: начал / прогресс / завершил / последняя активность
  context_notes    — таблица «Дополнительный контекст по врачам» из задания
  digest_headlines — верхние строки сводки с числами (3 врача, 5%; 29 из 33, 88% и т.д.)

Модуль намеренно не зависит ни от telegram, ни от каких-либо сетевых библиотек —
его можно тестировать отдельно (см. run_demo.py).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH") or os.path.join(BASE_DIR, "bot.db")

# Дата, на которую актуальна сводка («Сегодня 29 сентября»).
DIGEST_DATE = os.environ.get("DIGEST_DATE") or "2026-09-29"
DIGEST_TS = datetime.fromisoformat(DIGEST_DATE + "T09:00:00")

COURSE_KT = "Требования к описаниям КТ ОГК и ОБП"
COURSE_RR = "Знакомство с платформой RadReport"

TOTAL_DOCTORS = 66          # «всего врачей: 66»
ASSIGNED_PER_COURSE = 33    # «всего назначено: 33» по каждому курсу


# ----------------------------------------------------------------------------
# Данные для первичного наполнения (взяты из сводки и таблицы контекста)
# ----------------------------------------------------------------------------

SEED_COURSES = [
    (COURSE_KT, ASSIGNED_PER_COURSE),
    (COURSE_RR, ASSIGNED_PER_COURSE),
]


def _h(days: int, hours: int) -> int:
    """«51д 18ч» -> 1242 часа. Храним часы, чтобы не терять точность."""
    return days * 24 + hours


SEED_DOCTORS = [
    {
        "full_name": "Иванова Анна",
        "last_login": None,                      # «не залогинились ни разу»
        "notes": [
            ("Попросила паузу до начала июля — ложилась на операцию", "2026-06-25"),
            ("Пауза не пересогласована, повторных обращений не было", "2026-08-12"),
        ],
        "enrollments": [
            (COURSE_KT, dict(started=0, progress=0, completed=0, hours_inactive=_h(51, 18))),
            (COURSE_RR, dict(started=0, progress=0, completed=0, hours_inactive=_h(51, 16))),
        ],
    },
    {
        "full_name": "Сидорова Мария Александровна",
        "last_login": None,
        "notes": [
            ("Не прислала данные для оформления договора с ReStaff, "
             "нет понимания, есть ли самозанятость; доступы к онбординг-платформе выданы", "2026-09-16"),
            ("Напоминание о данных для договора отправлено, ответа нет", "2026-09-24"),
        ],
        "enrollments": [
            (COURSE_KT, dict(started=0, progress=0, completed=0, hours_inactive=_h(12, 21))),
            (COURSE_RR, dict(started=0, progress=0, completed=0, hours_inactive=_h(12, 21))),
        ],
    },
    {
        "full_name": "Смирнов Николай",
        "last_login": "2026-09-17T11:05:00",
        "notes": [
            ("Подписал договор на ReStaff", "2026-09-10"),
            ("Договор подписан, но к курсам не приступает", "2026-09-25"),
        ],
        "enrollments": [
            (COURSE_KT, dict(started=0, progress=0, completed=0, hours_inactive=_h(12, 16))),
            (COURSE_RR, dict(started=0, progress=0, completed=0, hours_inactive=_h(12, 16))),
        ],
    },
    {
        "full_name": "Петров Пётр Сергеевич",
        "last_login": None,
        "notes": [
            ("Испытывает трудности с регистрацией на платформе ReStaff", "2026-09-18"),
            ("Обращался в поддержку, вопрос не закрыт", "2026-09-23"),
        ],
        "enrollments": [
            (COURSE_KT, dict(started=0, progress=0, completed=0, hours_inactive=_h(11, 2))),
            (COURSE_RR, dict(started=0, progress=0, completed=0, hours_inactive=_h(11, 2))),
        ],
    },
    {
        "full_name": "Козлов Дмитрий Игоревич",
        "last_login": "2026-06-19T15:40:00",
        "notes": [
            ("До сих пор не зарегистрировался по приглашению на ReStaff, "
             "не выходил на связь с 20 июня", "2026-06-20"),
            ("Курс «Требования к описаниям КТ ОГК и ОБП» в сводке не назначен — "
             "нужно уточнить, выпал ли врач из программы по ОГК/ОБП", "2026-09-29"),
        ],
        # Внимание: назначен только на RadReport — в списках по КТ ОГК/ОБП его нет.
        "enrollments": [
            (COURSE_RR, dict(started=0, progress=0, completed=0, hours_inactive=_h(32, 21))),
        ],
    },
    {
        "full_name": "Фёдорова Елена",
        "last_login": "2026-09-28T09:35:00",
        "notes": [
            ("Завершила оба курса в течение последних 24 часов", "2026-09-28"),
        ],
        "enrollments": [
            (COURSE_KT, dict(started=1, progress=100, completed=1,
                             completed_at="2026-09-28T09:40:00")),
            (COURSE_RR, dict(started=1, progress=100, completed=1,
                             completed_at="2026-09-28T09:55:00")),
        ],
    },
    {
        "full_name": "Морозова Ольга",
        "last_login": "2026-09-28T11:15:00",
        "notes": [
            ("Курс RadReport завершён в последние 24 часа; КТ ОГК и ОБП — завершён ранее", "2026-09-28"),
        ],
        "enrollments": [
            (COURSE_KT, dict(started=1, progress=100, completed=1,
                             completed_at="2026-09-11T14:00:00")),
            (COURSE_RR, dict(started=1, progress=100, completed=1,
                             completed_at="2026-09-28T11:20:00")),
        ],
    },
    {
        "full_name": "Волкова Светлана Викторовна",
        "last_login": "2026-09-28T16:00:00",
        "notes": [
            ("Курс RadReport завершён в последние 24 часа; КТ ОГК и ОБП — завершён ранее", "2026-09-28"),
        ],
        "enrollments": [
            (COURSE_KT, dict(started=1, progress=100, completed=1,
                             completed_at="2026-09-08T12:30:00")),
            (COURSE_RR, dict(started=1, progress=100, completed=1,
                             completed_at="2026-09-28T16:05:00")),
        ],
    },
]


# Верхние строки сводки: (заголовок, пояснение, сколько врачей, процент)
SEED_HEADLINES = [
    ("🔴 Не залогинились ни разу", "всего врачей: 66", 3, 5),
    ("🔴 Не учатся 3 дня · КТ ОГК и ОБП", "всего назначено: 33", 4, 12),
    ("🔴 Не учатся 3 дня · RadReport", "всего назначено: 33", 6, 18),
    ("🟠 Не начали · КТ ОГК и ОБП", "от незавершивших", 4, 100),
    ("🟠 Не начали · RadReport", "от незавершивших", 5, 71),
    ("🟡 Нет активности 1–70% · КТ ОГК и ОБП", "от незавершивших", 0, 0),
    ("🟡 Нет активности 1–70% · RadReport", "от незавершивших", 0, 0),
    ("✅ Завершили · КТ ОГК и ОБП", "из 33 назначенных", 29, 88),
    ("✅ Завершили · RadReport", "из 33 назначенных", 29, 88),
]


# ----------------------------------------------------------------------------
# Схема
# ----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS doctors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL UNIQUE,
    name_lower    TEXT NOT NULL,
    last_login_at TEXT,               -- NULL = не залогинился ни разу
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_doctors_name_lower ON doctors(name_lower);

CREATE TABLE IF NOT EXISTS courses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL UNIQUE,
    assigned_total INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS enrollments (
    doctor_id        INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    course_id        INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    started          INTEGER NOT NULL DEFAULT 0,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    completed        INTEGER NOT NULL DEFAULT 0,
    completed_at     TEXT,
    last_activity_at TEXT,            -- NULL = активности не было вообще
    PRIMARY KEY (doctor_id, course_id)
);

CREATE TABLE IF NOT EXISTS context_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id  INTEGER NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
    body       TEXT NOT NULL,
    noted_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digest_headlines (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    title    TEXT NOT NULL,
    caption  TEXT NOT NULL,
    doctors  INTEGER NOT NULL,
    percent  INTEGER NOT NULL,
    position INTEGER NOT NULL
);
"""


def normalize(text: str) -> str:
    """Приводим ФИО к виду, удобному для поиска: нижний регистр, ё -> е, без лишних пробелов."""
    return " ".join(str(text).lower().replace("ё", "е").split())


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(force: bool = False) -> None:
    """
    Создаёт базу и наполняет её данными из сводки.

    force=True — пересоздать с нуля: удаляем файл и наполняем заново.
    Нужно, когда база лежит на постоянном диске (Railway Volume, VPS):
    без этого флага правки в SEED_* никогда не доедут до уже созданной базы.
    """
    # На Railway база лежит на подключённом диске (например /data/bot.db) —
    # каталога может ещё не быть, создаём заранее.
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if force and os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = connect()
    with conn:
        conn.executescript(SCHEMA)

        already = conn.execute("SELECT COUNT(*) AS n FROM doctors").fetchone()["n"]
        if already:
            return  # база уже наполнена — второй раз не трогаем

        now = datetime.now().isoformat(timespec="seconds")

        for title, assigned_total in SEED_COURSES:
            conn.execute(
                "INSERT INTO courses (title, assigned_total) VALUES (?, ?)",
                (title, assigned_total),
            )
        course_ids = {
            row["title"]: row["id"]
            for row in conn.execute("SELECT id, title FROM courses")
        }

        for doctor in SEED_DOCTORS:
            cur = conn.execute(
                "INSERT INTO doctors (full_name, name_lower, last_login_at, created_at) "
                "VALUES (?, ?, ?, ?)",
                (doctor["full_name"], normalize(doctor["full_name"]),
                 doctor.get("last_login"), now),
            )
            doctor_id = cur.lastrowid

            for note_body, note_date in doctor.get("notes", []):
                conn.execute(
                    "INSERT INTO context_notes (doctor_id, body, noted_at) VALUES (?, ?, ?)",
                    (doctor_id, note_body, f"{note_date}T10:00:00"),
                )

            for course_title, data in doctor["enrollments"]:
                if data.get("completed"):
                    last_activity = data.get("completed_at")
                elif data.get("hours_inactive") is not None:
                    # «N дней без активности» из сводки -> абсолютное время последнего действия
                    last_activity = (
                        DIGEST_TS - timedelta(hours=data["hours_inactive"])
                    ).isoformat(timespec="seconds")
                else:
                    last_activity = None

                conn.execute(
                    "INSERT INTO enrollments (doctor_id, course_id, started, progress_percent,"
                    " completed, completed_at, last_activity_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        doctor_id,
                        course_ids[course_title],
                        data.get("started", 0),
                        data.get("progress", 0),
                        data.get("completed", 0),
                        data.get("completed_at"),
                        last_activity,
                    ),
                )

        for position, (title, caption, doctors, percent) in enumerate(SEED_HEADLINES, start=1):
            conn.execute(
                "INSERT INTO digest_headlines (title, caption, doctors, percent, position) "
                "VALUES (?, ?, ?, ?, ?)",
                (title, caption, doctors, percent, position),
            )


# ----------------------------------------------------------------------------
# Чтение
# ----------------------------------------------------------------------------

def find_doctors(query: str, limit: int = 5) -> list[sqlite3.Row]:
    """
    Ищем врача по фамилии, имени или ФИО целиком.
    «Иванова», «иванов», «Петров Пётр», «мария» — всё должно находить.
    """
    q = normalize(query)
    if not q:
        return []

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, full_name, name_lower, last_login_at FROM doctors ORDER BY full_name"
        ).fetchall()

    tokens = [t for t in q.split() if len(t) >= 3]
    if not tokens:
        return []

    exact, partial = [], []
    for row in rows:
        words = row["name_lower"].split()
        # точное совпадение: каждый введённый токен совпадает с началом какого-то слова ФИО
        if all(any(w.startswith(t) for w in words) for t in tokens):
            if q == row["name_lower"]:
                exact.append(row)
            else:
                partial.append(row)

    return (exact + partial)[:limit]


def get_doctor(doctor_id: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM doctors WHERE id = ?", (doctor_id,)
        ).fetchone()


def get_notes(doctor_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT body, noted_at FROM context_notes WHERE doctor_id = ? ORDER BY noted_at",
            (doctor_id,),
        ).fetchall()


def get_enrollments(doctor_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT c.title, c.assigned_total, e.started, e.progress_percent,
                   e.completed, e.completed_at, e.last_activity_at
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            WHERE e.doctor_id = ?
            ORDER BY c.id
            """,
            (doctor_id,),
        ).fetchall()


def get_headlines() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT title, caption, doctors, percent FROM digest_headlines ORDER BY position"
        ).fetchall()


def list_doctors() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT id, full_name FROM doctors ORDER BY full_name"
        ).fetchall()


def course_totals() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT title, assigned_total FROM courses ORDER BY id").fetchall()


if __name__ == "__main__":
    init_db(force=True)
    print(f"База создана: {DB_PATH}")
    print(f"Врачей: {len(list_doctors())}, курсов: {len(course_totals())}, "
          f"строк сводки: {len(get_headlines())}")
