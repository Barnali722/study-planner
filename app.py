import os
import csv
import io
import sqlite3
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, jsonify, Response

app = Flask(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__), "study_planner.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT '#4f46e5',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS study_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,
            planned_minutes INTEGER NOT NULL DEFAULT 25,
            actual_minutes INTEGER,
            completed INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()


@app.route("/")
def dashboard():
    conn = get_db()
    today = date.today().isoformat()

    upcoming = conn.execute("""
        SELECT s.*, sub.name AS subject_name, sub.color AS subject_color
        FROM study_sessions s
        JOIN subjects sub ON s.subject_id = sub.id
        WHERE s.scheduled_date >= ? AND s.completed = 0
        ORDER BY s.scheduled_date ASC
        LIMIT 10
    """, (today,)).fetchall()

    completed_today = conn.execute("""
        SELECT COUNT(*) as cnt FROM study_sessions
        WHERE scheduled_date = ? AND completed = 1
    """, (today,)).fetchone()["cnt"]

    total_minutes_today = conn.execute("""
        SELECT COALESCE(SUM(actual_minutes), 0) as total FROM study_sessions
        WHERE scheduled_date = ? AND completed = 1
    """, (today,)).fetchone()["total"]

    total_sessions = conn.execute("SELECT COUNT(*) as cnt FROM study_sessions WHERE completed = 1").fetchone()["cnt"]
    subjects = conn.execute("SELECT * FROM subjects ORDER BY name").fetchall()
    conn.close()

    return render_template("index.html",
                           upcoming=upcoming,
                           completed_today=completed_today,
                           total_minutes_today=total_minutes_today,
                           total_sessions=total_sessions,
                           subjects=subjects,
                           today=today)


@app.route("/subjects", methods=["GET", "POST"])
def subjects():
    conn = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        color = request.form.get("color", "#4f46e5")
        if name:
            conn.execute("INSERT INTO subjects (name, color) VALUES (?, ?)", (name, color))
            conn.commit()
        conn.close()
        return redirect(url_for("subjects"))

    all_subjects = conn.execute("""
        SELECT sub.*, COUNT(s.id) as session_count
        FROM subjects sub
        LEFT JOIN study_sessions s ON s.subject_id = sub.id
        GROUP BY sub.id
        ORDER BY sub.name
    """).fetchall()
    conn.close()
    return render_template("subjects.html", subjects=all_subjects)


@app.route("/subjects/<int:subject_id>/delete", methods=["POST"])
def delete_subject(subject_id):
    conn = get_db()
    conn.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("subjects"))


@app.route("/sessions", methods=["GET", "POST"])
def sessions():
    conn = get_db()
    if request.method == "POST":
        subject_id = request.form.get("subject_id")
        title = request.form.get("title", "").strip()
        scheduled_date = request.form.get("scheduled_date")
        planned_minutes = request.form.get("planned_minutes", 25)
        notes = request.form.get("notes", "").strip()
        if subject_id and title and scheduled_date:
            conn.execute("""
                INSERT INTO study_sessions (subject_id, title, scheduled_date, planned_minutes, notes)
                VALUES (?, ?, ?, ?, ?)
            """, (subject_id, title, scheduled_date, planned_minutes, notes))
            conn.commit()
        conn.close()
        return redirect(url_for("sessions"))

    filter_date = request.args.get("date", "")
    filter_subject = request.args.get("subject", "")
    filter_status = request.args.get("status", "")

    query = """
        SELECT s.*, sub.name AS subject_name, sub.color AS subject_color
        FROM study_sessions s
        JOIN subjects sub ON s.subject_id = sub.id
        WHERE 1=1
    """
    params = []
    if filter_date:
        query += " AND s.scheduled_date = ?"
        params.append(filter_date)
    if filter_subject:
        query += " AND s.subject_id = ?"
        params.append(filter_subject)
    if filter_status == "completed":
        query += " AND s.completed = 1"
    elif filter_status == "pending":
        query += " AND s.completed = 0"

    query += " ORDER BY s.scheduled_date DESC, s.id DESC"
    all_sessions = conn.execute(query, params).fetchall()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY name").fetchall()
    conn.close()
    return render_template("sessions.html",
                           sessions=all_sessions,
                           subjects=subjects,
                           filter_date=filter_date,
                           filter_subject=filter_subject,
                           filter_status=filter_status)


@app.route("/sessions/<int:session_id>/complete", methods=["POST"])
def complete_session(session_id):
    actual_minutes = request.form.get("actual_minutes")
    conn = get_db()
    conn.execute("""
        UPDATE study_sessions SET completed = 1, actual_minutes = ? WHERE id = ?
    """, (actual_minutes, session_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("sessions"))


@app.route("/sessions/<int:session_id>/delete", methods=["POST"])
def delete_session(session_id):
    conn = get_db()
    conn.execute("DELETE FROM study_sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("sessions"))


@app.route("/timer")
def timer():
    conn = get_db()
    subjects = conn.execute("SELECT * FROM subjects ORDER BY name").fetchall()
    pending = conn.execute("""
        SELECT s.*, sub.name AS subject_name, sub.color AS subject_color
        FROM study_sessions s
        JOIN subjects sub ON s.subject_id = sub.id
        WHERE s.completed = 0
        ORDER BY s.scheduled_date ASC
        LIMIT 20
    """).fetchall()
    conn.close()
    return render_template("timer.html", subjects=subjects, pending_sessions=pending)


@app.route("/sessions/export")
def export_sessions():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.id, sub.name AS subject, s.title, s.scheduled_date,
               s.planned_minutes, s.actual_minutes,
               CASE WHEN s.completed = 1 THEN 'Completed' ELSE 'Pending' END AS status,
               s.notes, s.created_at
        FROM study_sessions s
        JOIN subjects sub ON s.subject_id = sub.id
        ORDER BY s.scheduled_date DESC
    """).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Subject", "Title", "Date", "Planned (min)", "Actual (min)", "Status", "Notes", "Created At"])
    for r in rows:
        writer.writerow(list(r))
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=study_sessions.csv"})


@app.route("/subjects/export")
def export_subjects():
    conn = get_db()
    rows = conn.execute("""
        SELECT sub.id, sub.name, sub.color,
               COUNT(s.id) AS total_sessions,
               SUM(CASE WHEN s.completed = 1 THEN 1 ELSE 0 END) AS completed_sessions,
               sub.created_at
        FROM subjects sub
        LEFT JOIN study_sessions s ON s.subject_id = sub.id
        GROUP BY sub.id
        ORDER BY sub.name
    """).fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Subject Name", "Color", "Total Sessions", "Completed Sessions", "Created At"])
    for r in rows:
        writer.writerow(list(r))
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=subjects.csv"})


@app.route("/api/session/<int:session_id>/log-time", methods=["POST"])
def log_time(session_id):
    data = request.get_json()
    minutes = data.get("minutes", 0)
    conn = get_db()
    conn.execute("""
        UPDATE study_sessions SET actual_minutes = COALESCE(actual_minutes, 0) + ?,
        completed = CASE WHEN ? >= planned_minutes THEN 1 ELSE completed END
        WHERE id = ?
    """, (minutes, minutes, session_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)
