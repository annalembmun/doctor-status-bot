"""
report.py — превращает записи из базы в человекочитаемый статус врача.

Здесь живёт вся «логика приоритета»: бот не просто вываливает цифры,
а говорит, насколько врач проблемный и что с ним делать дальше.
Зависимостей от Telegram нет — можно тестировать из консоли.
"""

from __future__ import annotations

from datetime import datetime

import db

PRIORITY_EMOJI = {"критично": "🔴", "высокий": "🟠", "средний": "🟡", "ок": "✅"}


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def _human_gap(value: str | None) -> str:
    """«12 дн 21 ч» — сколько прошло с момента последней активности."""
    if not value:
        return "активности не было ни разу"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    delta = db.DIGEST_TS - moment
    total_hours = int(delta.total_seconds() // 3600)
    if total_hours < 0:
        return "меньше суток назад"
    days, hours = divmod(total_hours, 24)
    if days == 0:
        return f"{hours} ч"
    return f"{days} дн {hours} ч"


def _course_status_text(row) -> tuple[str, str]:
    """Возвращает (строка статуса, метка для расчёта приоритета)."""
    progress = row["progress_percent"]
    if row["completed"]:
        return (
            f"завершён {_fmt_dt(row['completed_at'])[:10]}",
            "done",
        )
    if not row["started"]:
        return (
            f"не начат · {_human_gap(row['last_activity_at'])} без активности",
            "not_started",
        )
    if progress < 70:
        return (
            f"в процессе {progress}% · {_human_gap(row['last_activity_at'])} без активности",
            "stalled",
        )
    return (
        f"в процессе {progress}% · {_human_gap(row['last_activity_at'])} без активности",
        "stalled",
    )


def assess(doctor_row, enrollment_rows, note_rows) -> dict:
    """
    Считаем итоговую картину по врачу: приоритет + что делать.
    Правила приоритета (в порядке убывания важности):
      критично — ни одного входа ИЛИ завершённых курсов нет вообще
      высокий  — есть незавершённые курсы, которые даже не начаты
      средний  — курсы начаты, но прогресс < 70% и простаивают
      ок       — все назначенные курсы завершены
    """
    last_login = doctor_row["last_login_at"]
    never_logged_in = last_login is None

    states = [_course_status_text(r)[1] for r in enrollment_rows]
    completed_count = states.count("done")
    total_courses = len(states)

    if never_logged_in:
        priority, reason = "критично", "не заходил(а) на платформу ни разу"
    elif completed_count == total_courses and total_courses:
        priority, reason = "ок", "все назначенные курсы завершены"
    elif "not_started" in states:
        priority, reason = "высокий", "есть курс, к которому так и не приступил(а)"
    elif "stalled" in states:
        priority, reason = "средний", "курс начат, но прогресс ниже 70%"
    else:
        priority, reason = "ок", "все назначенные курсы завершены"

    # Продолжительность простоя: берём максимум по курсам
    max_gap_hours = 0
    for row in enrollment_rows:
        if row["last_activity_at"]:
            try:
                moment = datetime.fromisoformat(row["last_activity_at"])
                gap = int((db.DIGEST_TS - moment).total_seconds() // 3600)
                max_gap_hours = max(max_gap_hours, gap)
            except ValueError:
                pass

    return {
        "priority": priority,
        "emoji": PRIORITY_EMOJI[priority],
        "reason": reason,
        "never_logged_in": never_logged_in,
        "max_gap_hours": max_gap_hours,
        "completed_count": completed_count,
        "total_courses": total_courses,
        "notes": note_rows,
    }


def next_step(doctor_row, enrollment_rows, assessment) -> str:
    """Рекомендация «что делать дальше» — самая полезная часть ответа бота."""
    name = doctor_row["full_name"]
    notes_text = " ".join(n["body"].lower() for n in assessment["notes"])

    if assessment["priority"] == "ок":
        return "Ничего не требуется — можно писать короткое подтверждение и закрывать карточку."

    if assessment["never_logged_in"]:
        if "не прислал" in notes_text and "договор" in notes_text:
            return (
                f"Блокер на нашей стороне: ждём данные для договора. "
                f"Пингануть {name.split()[0]} лично и предложить помощь с оформлением, "
                f"зафиксировать дедлайн. Если молчание ещё 3 рабочих дня — эскалация куратору."
            )
        if "трудност" in notes_text and "регистрац" in notes_text:
            return (
                "Технический блокер: помочь с регистрацией на ReStaff (созвон или скриншоты шагов), "
                "после разблокировки сразу вернуть к первому курсу."
            )
        if "пауз" in notes_text or "операц" in notes_text:
            return (
                "Ранее согласованная пауза истекла. Написать с уважением к ситуации, "
                "переспросить сроки и заново согласовать дату возврата."
            )
        return (
            "Первый контакт: выяснить причину (доступы, загрузка, мотивация), "
            "предложить личный созвон на 10 минут."
        )

    if assessment["priority"] == "высокий":
        if "подписал договор" in notes_text:
            return (
                "Договор подписан, но обучение не начато — оплата за врачом уже идёт. "
                "Написать с конкретным шагом: «откройте модуль 1, это 15 минут», "
                "назначить контрольную дату через 2 рабочих дня."
            )
        if "регистрац" in notes_text:
            return "Помочь дописать регистрацию на ReStaff и сразу выдать доступ к курсу."
        return (
            "Написать в Telegram с прямым призывом начать и назвать срок; "
            "если не стартует за 2 рабочих дня — звонок куратора."
        )

    if assessment["priority"] == "средний":
        return (
            "Курс начат, но встал на паузу. Уточнить, что именно остановило, "
            "и предложить пройти оставшиеся модули до конца недели."
        )

    return "Проверить вручную."


def build_report(full_name: str) -> str:
    """Готовый текст ответа бота по фамилии врача."""
    rows = db.find_doctors(full_name)
    if not rows:
        return (
            f"Не нашёл врача по запросу «{full_name}».\n"
            "Попробуйте фамилию или имя — например: Иванова, Сидорова, Петров.\n"
            "Показать всех: /list"
        )

    if len(rows) > 1:
        variants = "\n".join(f"• {r['full_name']}" for r in rows)
        return f"Нашёл несколько врачей:\n{variants}\n\nУточните, пожалуйста, фамилию или имя."

    doctor = db.get_doctor(rows[0]["id"])
    enrollments = db.get_enrollments(doctor["id"])
    notes = db.get_notes(doctor["id"])
    a = assess(doctor, enrollments, notes)

    lines: list[str] = []
    lines.append(f"{a['emoji']} {doctor['full_name']}")
    lines.append(f"Приоритет: {a['priority']} — {a['reason']}")
    if a["never_logged_in"]:
        lines.append("Последний вход: ни разу на платформе")
    else:
        lines.append(
            f"Последний вход: {_fmt_dt(doctor['last_login_at'])} "
            f"({_human_gap(doctor['last_login_at'])} назад)"
        )

    lines.append("")
    lines.append(f"📚 Курсы ({a['completed_count']} из {a['total_courses']} завершено):")
    if not enrollments:
        lines.append("  • назначений нет — уточнить, должен ли врач быть в программе")
    for row in enrollments:
        text, _ = _course_status_text(row)
        lines.append(f"  • {row['title']}: {text}")

    lines.append("")
    lines.append("🗒 Контекст:")
    if notes:
        for n in notes:
            lines.append(f"  • {_fmt_dt(n['noted_at'])[:10]} — {n['body']}")
    else:
        lines.append("  • дополнительных записей нет")

    lines.append("")
    lines.append(f"➡️ Следующий шаг: {next_step(doctor, enrollments, a)}")

    return "\n".join(lines)


def build_overview() -> str:
    """Полная картина по сводке — ответ на /summary и /list."""
    lines = [f"📊 Сводка на {db.DIGEST_DATE} · врачей всего: {db.TOTAL_DOCTORS}", ""]
    for h in db.get_headlines():
        lines.append(f"{h['title']}: {h['doctors']} ({h['percent']}%, {h['caption']})")

    lines.append("")
    lines.append("👤 Врачи, требующие внимания:")
    for row in db.list_doctors():
        doctor = db.get_doctor(row["id"])
        enrollments = db.get_enrollments(row["id"])
        a = assess(doctor, enrollments, db.get_notes(row["id"]))
        if a["priority"] != "ок":
            lines.append(f"  {a['emoji']} {doctor['full_name']} — {a['reason']}")
    return "\n".join(lines)


if __name__ == "__main__":
    db.init_db()
    for query in ["Иванова", "Смирнов", "Козлов"]:
        print("=" * 70)
        print(build_report(query))
        print()
    print("=" * 70)
    print(build_overview())
