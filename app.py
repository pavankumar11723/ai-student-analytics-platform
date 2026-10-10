from flask import (
    Flask, render_template, jsonify, request,
    session, redirect, url_for, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import sqlite3
import math
from datetime import datetime, timedelta, date
from apscheduler.schedulers.background import BackgroundScheduler
from sklearn.tree import DecisionTreeClassifier
from ai_engine import generate_insight
from getpass import getpass

app = Flask(__name__)

app.secret_key = os.environ.get("FLASK_SECRET_KEY")

if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY environment variable is required")

DATABASE = "students.db"

# Semester and report configuration
SEMESTER_START_DATE = "2026-07-01"
SEMESTER_END_DATE = "2026-11-10"
FORTNIGHT_DAYS = 15
MONTHLY_REPORT = True

SNAPSHOT_INTERVAL_MINUTES = 1
SCHEDULER_INTERVAL_SECONDS = 30


def semester_dates():
    return date.fromisoformat(SEMESTER_START_DATE), date.fromisoformat(SEMESTER_END_DATE)


def semester_contains(day):
    start, end = semester_dates()
    return start <= day <= end


def report_periods():
    start, end = semester_dates()
    periods = []
    period_start = start
    period_number = 1

    while period_start <= end:
        period_end = min(
            period_start + timedelta(days=FORTNIGHT_DAYS - 1), end
        )
        periods.append({
            "report_type": "fortnightly",
            "period_number": period_number,
            "start_date": period_start.isoformat(),
            "end_date": period_end.isoformat()
        })
        period_start = period_end + timedelta(days=1)
        period_number += 1

    if MONTHLY_REPORT:
        month_start = start.replace(day=1)

        while month_start <= end:
            if month_start.month == 12:
                next_month = date(month_start.year + 1, 1, 1)
            else:
                next_month = date(
                    month_start.year, month_start.month + 1, 1
                )

            month_end = next_month - timedelta(days=1)
            clipped_start = max(month_start, start)
            clipped_end = min(month_end, end)

            if clipped_start <= clipped_end:
                periods.append({
                    "report_type": "monthly",
                    "period_number": month_start.month,
                    "start_date": clipped_start.isoformat(),
                    "end_date": clipped_end.isoformat()
                })

            month_start = next_month

    return periods

# Machine learning model training data
# Features: [attendance, score, study_hours]

X = [
    [40, 35, 1],
    [45, 42, 2],
    [50, 48, 2],
    [55, 52, 2],
    [60, 58, 3],
    [65, 62, 3],
    [70, 68, 4],
    [75, 72, 4],
    [80, 78, 5],
    [85, 82, 5],
    [90, 88, 6],
    [95, 92, 7],

    # Additional examples: very low study time
    [90, 71, 1],
    [95, 85, 1],
    [80, 75, 1],
    [85, 70, 1]
]

y = [
    "High", "High", "High", "High", "High",
    "Medium", "Medium", "Medium",
    "Low", "Low", "Low", "Low",

    # Labels for the additional examples
    "High", "High", "High", "High"
]

model = DecisionTreeClassifier(random_state=42)
model.fit(X, y)

# Database connection
def get_connection():
    connection = sqlite3.connect(DATABASE, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def current_timestamp():
    return datetime.now().isoformat(sep=" ", timespec="microseconds")


# Database initialization


# Database initialization
def initialize_database():
    connection = get_connection()

    try:
        table = connection.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'students'
        """).fetchone()

        if table is None:
            connection.execute("""
                CREATE TABLE students (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    attendance INTEGER,
                    score INTEGER,
                    risk TEXT,
                    study_hours INTEGER DEFAULT 2,
                    performance_updated_at TEXT,
                    assignment REAL
                )
            """)

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(students)"
            ).fetchall()
        }

        if "performance_updated_at" not in columns:
            connection.execute("""
                ALTER TABLE students
                ADD COLUMN performance_updated_at TEXT
            """)

        if "assignment" not in columns:
            connection.execute("""
                ALTER TABLE students
                ADD COLUMN assignment REAL
            """)

        connection.execute("""
            UPDATE students
            SET performance_updated_at = ?
            WHERE performance_updated_at IS NULL
        """, (current_timestamp(),))

        # Student progress history
        connection.execute("""
            CREATE TABLE IF NOT EXISTS student_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                attendance REAL NOT NULL,
                score REAL NOT NULL,
                study_hours REAL NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id)
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_student_progress_student_date
            ON student_progress(student_id, recorded_at)
        """)

        # Attendance records
        connection.execute("""
            CREATE TABLE IF NOT EXISTS attendance_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN ('Present', 'Absent')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id),
                UNIQUE(student_id, attendance_date)
            )
        """)

        # Attendance reports
        connection.execute("""
            CREATE TABLE IF NOT EXISTS attendance_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL
                    CHECK(report_type IN ('fortnightly', 'monthly')),
                period_number INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                UNIQUE(report_type, period_start, period_end)
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS attendance_report_students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                present_days INTEGER NOT NULL,
                recorded_days INTEGER NOT NULL,
                period_percentage REAL,
                semester_present_days INTEGER NOT NULL,
                semester_recorded_days INTEGER NOT NULL,
                semester_percentage REAL,
                FOREIGN KEY (report_id)
                    REFERENCES attendance_reports(id),
                FOREIGN KEY (student_id) REFERENCES students(id),
                UNIQUE(report_id, student_id)
            )
        """)

        # Marks management tables
        connection.execute("""
            CREATE TABLE IF NOT EXISTS assessments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                academic_year TEXT NOT NULL,
                semester INTEGER NOT NULL
                    CHECK(semester BETWEEN 1 AND 8),
                assessment_type TEXT NOT NULL
                    CHECK(assessment_type IN ('Internal', 'Semester')),
                subject TEXT NOT NULL,
                max_marks REAL NOT NULL CHECK(max_marks > 0),
                assessment_date TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS student_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                assessment_id INTEGER NOT NULL,
                marks_obtained REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id),
                FOREIGN KEY (assessment_id) REFERENCES assessments(id),
                UNIQUE(student_id, assessment_id)
            )
        """)

        # Placement analytics
        connection.execute("""
            CREATE TABLE IF NOT EXISTS placement_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL UNIQUE,
                aptitude_score REAL NOT NULL DEFAULT 0
                    CHECK(aptitude_score BETWEEN 0 AND 100),
                coding_score REAL NOT NULL DEFAULT 0
                    CHECK(coding_score BETWEEN 0 AND 100),
                communication_score REAL NOT NULL DEFAULT 0
                    CHECK(communication_score BETWEEN 0 AND 100),
                mock_interview_score REAL NOT NULL DEFAULT 0
                    CHECK(mock_interview_score BETWEEN 0 AND 100),
                placement_status TEXT NOT NULL DEFAULT 'Not Placed'
                    CHECK(placement_status IN (
                        'Not Placed', 'Placed', 'In Progress'
                    )),
                company_name TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id)
                    REFERENCES students(id)
                    ON DELETE CASCADE
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_placement_student
            ON placement_records(student_id)
        """)

        # Accounts for admin, staff, and students
        connection.execute("""
            CREATE TABLE IF NOT EXISTS user_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK(role IN ('admin', 'staff', 'student')),
                student_id INTEGER UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id),
                CHECK (
                    (role = 'student' AND student_id IS NOT NULL)
                    OR
                    (role IN ('admin', 'staff') AND student_id IS NULL)
                )
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_accounts_role
            ON user_accounts(role)
        """)

        # Preserve existing progress snapshots.
        # Add baseline history only for students without history.
        students = connection.execute("""
            SELECT id, attendance, score, study_hours
            FROM students
        """).fetchall()

        for student in students:
            existing = connection.execute("""
                SELECT id FROM student_progress
                WHERE student_id = ? LIMIT 1
            """, (student["id"],)).fetchone()

            if existing is None:
                connection.execute("""
                    INSERT INTO student_progress (
                        student_id,
                        attendance,
                        score,
                        study_hours,
                        recorded_at
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    student["id"],
                    student["attendance"] if student["attendance"] is not None else 0,
                    student["score"] if student["score"] is not None else 0,
                    student["study_hours"] if student["study_hours"] is not None else 2,
                    current_timestamp()
                ))

        connection.commit()
        print("Database initialization completed.")
        print("Existing student data has been preserved.")
        print("User accounts table is ready.")

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# Check whether a user is logged in
def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view


# Restrict access to specific roles
def role_required(*allowed_roles):
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login"))

            if session.get("role") not in allowed_roles:
                return "Access denied: insufficient permissions", 403

            return view(*args, **kwargs)
        return wrapped_view
    return decorator


# Create the first admin account only when one doesn't exist
def create_admin_account(username, password):
    connection = get_connection()

    try:
        existing_admin = connection.execute("""
            SELECT id FROM user_accounts
            WHERE role = 'admin'
            LIMIT 1
        """).fetchone()

        if existing_admin:
            print("An admin account already exists.")
            return

        connection.execute("""
            INSERT INTO user_accounts
                (username, password_hash, role, student_id)
            VALUES (?, ?, 'admin', NULL)
        """, (
            username,
            generate_password_hash(password)
        ))

        connection.commit()
        print("Admin account created successfully.")

    except sqlite3.IntegrityError:
        connection.rollback()
        print("That username already exists. Choose another username.")

    finally:
        connection.close()

# Create the Render admin account from environment variables
import os

admin_username = os.getenv("ADMIN_USERNAME")
admin_password = os.getenv("ADMIN_PASSWORD")

if admin_username and admin_password:
    create_admin_account(admin_username, admin_password)
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        connection = get_connection()
        try:
            user = connection.execute("""
                SELECT id, username, password_hash, role, student_id
                FROM user_accounts
                WHERE username = ? COLLATE NOCASE
            """, (username,)).fetchone()
        finally:
            connection.close()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["student_id"] = user["student_id"]

            if user["role"] == "admin":
                return redirect(url_for("index"))

            elif user["role"] == "staff":
                return redirect(url_for("staff_dashboard"))

            elif user["role"] == "student":
                return redirect(
                    url_for(
                        "student_page",
                        student_id=user["student_id"]
                    )
                )

        flash("Invalid username or password.")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================
# ADMIN: CREATE STAFF AND STUDENT ACCOUNTS
# ============================================================

@app.route("/admin/accounts", methods=["GET", "POST"])

@login_required
@role_required("admin", "staff")
def manage_accounts():
    current_role = session.get("role")

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "").strip()
        student_id_value = request.form.get("student_id", "").strip()

        # Staff can create student accounts only.
        if current_role == "staff" and role != "student":
            flash("Staff can create student accounts only.")
            return redirect(url_for("manage_accounts"))

        # Admin can create staff or student accounts.
        if current_role == "admin" and role not in ("staff", "student"):
            flash("Choose a valid account role.")
            return redirect(url_for("manage_accounts"))

        if not username or not password:
            flash("Username and password are required.")
            return redirect(url_for("manage_accounts"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for("manage_accounts"))

        student_id = None

        if role == "student":
            try:
                student_id = int(student_id_value)
            except (ValueError, TypeError):
                flash("Enter a valid existing student ID.")
                return redirect(url_for("manage_accounts"))

        connection = get_connection()

        try:
            if role == "student":
                student = connection.execute(
                    "SELECT id FROM students WHERE id = ?",
                    (student_id,)
                ).fetchone()

                if student is None:
                    flash("That student ID does not exist.")
                    return redirect(url_for("manage_accounts"))

            connection.execute("""
                INSERT INTO user_accounts
                    (username, password_hash, role, student_id)
                VALUES (?, ?, ?, ?)
            """, (
                username,
                generate_password_hash(password),
                role,
                student_id
            ))

            connection.commit()
            flash(f"{role.title()} account created successfully.")

        except sqlite3.IntegrityError:
            connection.rollback()
            flash(
                "Username already exists, or this student "
                "already has an account."
            )

        finally:
            connection.close()

        return redirect(url_for("manage_accounts"))

    connection = get_connection()

    try:
        # Staff see student accounts only; admins see all accounts.
        if current_role == "admin":
            accounts = connection.execute("""
                SELECT
                    ua.id,
                    ua.username,
                    ua.role,
                    ua.student_id,
                    s.name AS student_name,
                    ua.created_at
                FROM user_accounts ua
                LEFT JOIN students s ON s.id = ua.student_id
                ORDER BY ua.id
            """).fetchall()
        else:
            accounts = connection.execute("""
                SELECT
                    ua.id,
                    ua.username,
                    ua.role,
                    ua.student_id,
                    s.name AS student_name,
                    ua.created_at
                FROM user_accounts ua
                LEFT JOIN students s ON s.id = ua.student_id
                WHERE ua.role = 'student'
                ORDER BY ua.id
            """).fetchall()

        students = connection.execute("""
            SELECT id, name
            FROM students
            ORDER BY id
        """).fetchall()

        return render_template(
            "admin_accounts.html",
            accounts=accounts,
            students=students,
            current_role=current_role
        )

    finally:
        connection.close()


# Attendance calculations

def attendance_stats(connection, student_id, start_date, end_date):
    result = connection.execute("""
        SELECT
            COUNT(*) AS recorded_days,
            COALESCE(
                SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END),
                0
            ) AS present_days
        FROM attendance_records
        WHERE student_id = ?
          AND attendance_date >= ?
          AND attendance_date <= ?
    """, (
        student_id,
        start_date.isoformat(),
        end_date.isoformat()
    )).fetchone()

    recorded_days = result["recorded_days"]
    present_days = result["present_days"]

    percentage = (
        round(present_days / recorded_days * 100, 2)
        if recorded_days else None
    )

    return {
        "present_days": present_days,
        "recorded_days": recorded_days,
        "attendance_percentage": percentage
    }


def semester_attendance(connection, student_id, as_of=None):
    start, end = semester_dates()
    today = as_of or date.today()
    effective_end = min(today, end)

    if effective_end < start:
        return {
            "present_days": 0,
            "recorded_days": 0,
            "attendance_percentage": None,
            "semester_start": start.isoformat(),
            "semester_end": end.isoformat()
        }

    stats = attendance_stats(
        connection, student_id, start, effective_end
    )

    return {
        **stats,
        "semester_start": start.isoformat(),
        "semester_end": end.isoformat()
    }


def effective_attendance(connection, student):
    """Return semester attendance, falling back to the stored value if none exists."""
    stats = semester_attendance(connection, student["id"])

    if stats["attendance_percentage"] is not None:
        return round(float(stats["attendance_percentage"]), 2)

    return round(float(student["attendance"] or 0), 2)


def effective_score(connection, student):
    """Use faculty-entered assessment marks when available; otherwise use stored score."""
    marks_summary = connection.execute("""
        SELECT
            COALESCE(SUM(sm.marks_obtained), 0) AS total_obtained,
            COALESCE(SUM(a.max_marks), 0) AS total_maximum
        FROM student_marks AS sm
        JOIN assessments AS a ON a.id = sm.assessment_id
        WHERE sm.student_id = ?
    """, (student["id"],)).fetchone()

    total_obtained = float(marks_summary["total_obtained"] or 0)
    total_maximum = float(marks_summary["total_maximum"] or 0)

    if total_maximum > 0:
        return round(total_obtained * 100.0 / total_maximum, 2)

    return round(float(student["score"] or 0), 2)


def student_analytics_values(connection, student):
    """Central source of truth for values used by profile, dashboard, and API."""
    attendance = effective_attendance(connection, student)
    score = effective_score(connection, student)
    study_hours = float(student["study_hours"] or 0)
    prediction = model.predict([[attendance, score, study_hours]])[0]
    insight = generate_insight(attendance, score, study_hours, prediction)

    return {
        "attendance": attendance,
        "score": score,
        "study_hours": study_hours,
        "predicted_risk": prediction,
        "risk": prediction,
        "explanation": insight.get("explanation", ""),
        "recommendation": insight.get("recommendation", ""),
        "priority": insight.get("priority", "")
    }

    
def generate_placement_report(placement):
    """Generate a placement performance report and recommendations."""

    if placement is None:
        return {
            "assessed": False,
            "readiness": None,
            "strongest_skill": None,
            "weakest_skill": None,
            "recommendations": [
                "Complete a placement assessment to receive "
                "personalized recommendations."
            ]
        }

    skills = {
        "Aptitude": float(placement["aptitude_score"]),
        "Coding": float(placement["coding_score"]),
        "Communication": float(placement["communication_score"]),
        "Mock Interview": float(placement["mock_interview_score"])
    }

    readiness = round(sum(skills.values()) / len(skills), 2)

    strongest_skill = max(skills, key=skills.get)
    weakest_skill = min(skills, key=skills.get)

    recommendations = []

    if skills["Aptitude"] < 70:
        recommendations.append(
            "Aptitude: Practice quantitative aptitude, logical "
            "reasoning, and timed placement questions regularly."
        )

    if skills["Coding"] < 70:
        recommendations.append(
            "Coding: Strengthen Java fundamentals and practise "
            "arrays, strings, collections, and DSA problems."
        )

    if skills["Communication"] < 70:
        recommendations.append(
            "Communication: Practise explaining technical concepts "
            "clearly and answering HR interview questions aloud."
        )

    if skills["Mock Interview"] < 70:
        recommendations.append(
            "Mock interviews: Complete regular mock interviews, "
            "review mistakes, and practise answering under time limits."
        )

    if not recommendations:
        recommendations.append(
            "Maintain your current preparation level and continue "
            "practising timed coding tests and mock interviews."
        )

    return {
        "assessed": True,
        "readiness": readiness,
        "strongest_skill": strongest_skill,
        "weakest_skill": weakest_skill,
        "recommendations": recommendations
    }


# Performance validation
def validate_performance(data):
    try:
        attendance = float(data["attendance"])
        score = float(data["score"])
        study_hours = float(data["study_hours"])

        assignment = data.get("assignment")
        if assignment is not None:
            assignment = float(assignment)

    except (KeyError, TypeError, ValueError):
        return None, (
            "Attendance, score, and study hours must be valid numbers. "
            "Assignment must also be numeric if provided."
        )

    values = [attendance, score, study_hours]

    if assignment is not None:
        values.append(assignment)

    if not all(math.isfinite(value) for value in values):
        return None, "Values must be finite numbers."

    if not 0 <= attendance <= 100:
        return None, "Attendance must be between 0 and 100."

    if not 0 <= score <= 100:
        return None, "Academic score must be between 0 and 100."

    if not 0 <= study_hours <= 24:
        return None, "Study hours must be between 0 and 24."

    if assignment is not None and not 0 <= assignment <= 100:
        return None, "Assignment score must be between 0 and 100."

    return {
        "attendance": attendance,
        "score": score,
        "study_hours": study_hours,
        "assignment": assignment
    }, None


# Student progress calculations
def calculate_progress(current, previous):
    if current is None or previous is None:
        return {
            "status": "No Previous Record",
            "attendance_change": None,
            "score_change": None,
            "study_hours_change": None,
            "message": (
                "Record student performance again to track progress."
            )
        }

    attendance_change = (
        current["attendance"] - previous["attendance"]
    )
    score_change = current["score"] - previous["score"]
    study_hours_change = (
        current["study_hours"] - previous["study_hours"]
    )

    performance_change = attendance_change + score_change

    if attendance_change > 0 and score_change > 0:
        status = "Improving"
        message = (
            "Both attendance and academic score have improved."
        )
    elif attendance_change < 0 and score_change < 0:
        status = "Declining"
        message = (
            "Both attendance and academic score have decreased."
        )
    elif performance_change > 0:
        status = "Improving"
        message = (
            "Overall attendance and academic performance have improved."
        )
    elif performance_change < 0:
        status = "Declining"
        message = (
            "Overall attendance and academic performance have declined."
        )
    else:
        status = "No Change"
        message = (
            "Overall attendance and academic score are unchanged."
        )

    return {
        "status": status,
        "attendance_change": round(attendance_change, 2),
        "score_change": round(score_change, 2),
        "study_hours_change": round(study_hours_change, 2),
        "message": message
    }



def get_low_risk_count(student_data):
    return sum(
        1 for student in student_data
        if student.get("risk") == "Low"
    )

    improving_count = 0

    for student in students:
        progress = connection.execute("""
            SELECT attendance, score, study_hours
            FROM student_progress
            WHERE student_id = ?
            ORDER BY recorded_at DESC, id DESC
            LIMIT 2
        """, (student["id"],)).fetchall()

        latest = dict(progress[0]) if len(progress) >= 1 else None
        previous = dict(progress[1]) if len(progress) >= 2 else None

        result = calculate_progress(latest, previous)

        if result["status"] == "Improving":
            improving_count += 1

    return improving_count


def get_student_progress(connection, student_id):
    records = connection.execute("""
        SELECT id, student_id, attendance, score,
               study_hours, recorded_at
        FROM student_progress
        WHERE student_id = ?
        ORDER BY recorded_at DESC, id DESC
        LIMIT 2
    """, (student_id,)).fetchall()

    if not records:
        return {
            "latest": None,
            "previous": None,
            "progress": calculate_progress(None, None)
        }

    latest = dict(records[0])
    previous = dict(records[1]) if len(records) > 1 else None

    return {
        "latest": latest,
        "previous": previous,
        "progress": calculate_progress(latest, previous)
    }
    # ============================================================
# ATTENDANCE REPORT GENERATION
# ============================================================

def generate_attendance_reports():
    connection = get_connection()

    try:
        today = date.today()
        start, end = semester_dates()
        effective_end = min(today, end)

        if effective_end < start:
            return

        students = connection.execute("""
            SELECT id, name
            FROM students
            ORDER BY id
        """).fetchall()

        periods = report_periods()

        for period in periods:
            period_start = date.fromisoformat(period["start_date"])
            period_end = date.fromisoformat(period["end_date"])

            # Do not generate a report for an unfinished period.
            if period_end > effective_end:
                continue

            existing = connection.execute("""
                SELECT id
                FROM attendance_reports
                WHERE report_type = ?
                  AND period_start = ?
                  AND period_end = ?
            """, (
                period["report_type"],
                period["start_date"],
                period["end_date"]
            )).fetchone()

            if existing:
                continue

            cursor = connection.execute("""
                INSERT INTO attendance_reports (
                    report_type,
                    period_number,
                    period_start,
                    period_end,
                    generated_at
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                period["report_type"],
                period["period_number"],
                period["start_date"],
                period["end_date"],
                current_timestamp()
            ))

            report_id = cursor.lastrowid

            for student in students:
                period_stats = attendance_stats(
                    connection,
                    student["id"],
                    period_start,
                    period_end
                )

                semester_stats = attendance_stats(
                    connection,
                    student["id"],
                    start,
                    effective_end
                )

                connection.execute("""
                    INSERT INTO attendance_report_students (
                        report_id,
                        student_id,
                        present_days,
                        recorded_days,
                        period_percentage,
                        semester_present_days,
                        semester_recorded_days,
                        semester_percentage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    report_id,
                    student["id"],
                    period_stats["present_days"],
                    period_stats["recorded_days"],
                    period_stats["attendance_percentage"],
                    semester_stats["present_days"],
                    semester_stats["recorded_days"],
                    semester_stats["attendance_percentage"]
                ))

        connection.commit()

    except Exception as error:
        connection.rollback()
        print("Attendance report error:", error)

    finally:
        connection.close()


def create_progress_snapshots():
    """Save progress snapshots using faculty-entered attendance and marks.

    Existing snapshots are preserved. This function only inserts new snapshots.
    """
    connection = get_connection()

    try:
        students = connection.execute("""
            SELECT id, study_hours
            FROM students
            ORDER BY id
        """).fetchall()

        timestamp = current_timestamp()

        for student in students:
            student_id = student["id"]

            # Use the semester attendance records entered by faculty.
            attendance_stats = semester_attendance(connection, student_id)
            attendance = (
                attendance_stats["attendance_percentage"]
                if attendance_stats["attendance_percentage"] is not None
                else 0
            )

            # Overall percentage across all marks entered by faculty.
            marks_summary = connection.execute("""
                SELECT
                    COALESCE(SUM(sm.marks_obtained), 0) AS total_obtained,
                    COALESCE(SUM(a.max_marks), 0) AS total_maximum
                FROM student_marks AS sm
                JOIN assessments AS a
                    ON a.id = sm.assessment_id
                WHERE sm.student_id = ?
            """, (student_id,)).fetchone()

            total_obtained = marks_summary["total_obtained"]
            total_maximum = marks_summary["total_maximum"]
            score = (
                round(total_obtained * 100.0 / total_maximum, 2)
                if total_maximum > 0
                else 0
            )

            study_hours = student["study_hours"] or 0

            connection.execute("""
                INSERT INTO student_progress (
                    student_id,
                    attendance,
                    score,
                    study_hours,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                student_id,
                attendance,
                score,
                study_hours,
                timestamp
            ))

        connection.commit()

    except Exception as error:
        connection.rollback()
        app.logger.exception("Progress snapshot error: %s", error)

    finally:
        connection.close()


# ============================================================
# PAGE ROUTES
# ============================================================

# ============================================================
# DASHBOARD ROUTES
# ============================================================

@app.route("/")
@login_required
def index():
    role = session.get("role")

    if role == "admin":
        return render_template("index.html")

    elif role == "staff":
        return redirect(url_for("staff_dashboard"))

    elif role == "student":
        return redirect(url_for("student_dashboard"))

    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/dashboard")
@login_required
@role_required("admin")
def admin_dashboard():
    return redirect(url_for("index"))


@app.route("/staff/dashboard")
@login_required
@role_required("staff")
def staff_dashboard():
    return render_template("staff_dashboard.html")


@app.route("/student/dashboard")
@login_required
@role_required("student")
def student_dashboard():
    return render_template("student_dashboard.html")


@app.route("/attendance")
def attendance_page():
    return render_template("attendance.html")


@app.route("/marks")
@login_required
@role_required("admin", "staff")
def marks_page():
    return render_template("marks.html")


@app.route("/student/<int:student_id>")
@login_required
def student_page(student_id):
    role = session.get("role")

    if role == "student" and session.get("student_id") != student_id:
        return "Access denied", 403

    if role not in ("admin", "staff", "student"):
        return "Access denied", 403

    connection = get_connection()

    try:
        student_row = connection.execute("""
            SELECT *
            FROM students
            WHERE id = ?
        """, (student_id,)).fetchone()

        if student_row is None:
            return "Student not found", 404

        student = dict(student_row)
        analytics = student_analytics_values(connection, student_row)

        
        placement_row = connection.execute("""
            SELECT *
            FROM placement_records
            WHERE student_id = ?
        """, (student_id,)).fetchone()

        placement = (
            dict(placement_row)
            if placement_row is not None
            else None
        )

        placement_report = generate_placement_report(placement)

        return render_template(
            "student.html",
            student=student,
            student_id=student["id"],
            name=student["name"],
            attendance=analytics["attendance"],
            score=analytics["score"],
            study_hours=analytics["study_hours"],
            risk=analytics["predicted_risk"],
            priority=analytics["priority"] or "NORMAL",
            explanation=analytics["explanation"],
            recommendation=analytics["recommendation"],
            placement=placement,
            placement_report=placement_report,
            current_role=session.get("role", "")
        )

    finally:
        connection.close()

# ============================================================
# DASHBOARD API
# ============================================================

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    connection = get_connection()

    try:
        students = connection.execute("""
            SELECT *
            FROM students
            ORDER BY id
        """).fetchall()

        total_students = len(students)
        student_data = []
        attendance_values = []
        score_values = []
        high_risk_names = []

        for student in students:
            student_dict = dict(student)
            analytics = student_analytics_values(connection, student)
            student_dict.update(analytics)

            attendance_values.append(analytics["attendance"])
            score_values.append(analytics["score"])

            if analytics["predicted_risk"] == "High":
                high_risk_names.append(student_dict["name"])

            student_data.append(student_dict)

        average_attendance = (
            round(sum(attendance_values) / total_students, 2)
            if total_students else 0
        )
        average_score = (
            round(sum(score_values) / total_students, 2)
            if total_students else 0
        )

        if not students:
            ai_insight = "No student records are available for analysis."
        elif high_risk_names:
            ai_insight = (
                f"{len(high_risk_names)} student(s) are classified as high risk: "
                + ", ".join(high_risk_names)
                + ". Review their attendance, academic scores, and study hours."
            )
        else:
            ai_insight = (
                "No students are currently classified as high risk. "
                "Continue monitoring their academic performance."
            )

        return jsonify({
            "total_students": total_students,
            "average_attendance": average_attendance,
            "average_score": average_score,
            "success_rate": average_score,
            "at_risk": len(high_risk_names),
            "improving": get_low_risk_count(student_data),
            "ai_insight": ai_insight,
            "students": student_data,
            "student_insights": student_data
        })

    except Exception:
        app.logger.exception("Dashboard analytics failed")
        return jsonify({
            "error": "Unable to generate dashboard analytics."
        }), 500

    finally:
        connection.close()

# ============================================================
# STUDENT API
# ============================================================

@app.route("/api/students", methods=["GET"])
def get_students():
    connection = get_connection()

    try:
        students = connection.execute("""
            SELECT *
            FROM students
            ORDER BY id
        """).fetchall()

        result = []

        for student in students:
            student_data = dict(student)
            analytics = student_analytics_values(connection, student)
            student_data.update(analytics)
            # Kept for compatibility with any existing frontend code.
            student_data["effective_attendance"] = analytics["attendance"]
            result.append(student_data)

        return jsonify(result)

    except Exception:
        app.logger.exception("Student API failed")
        return jsonify({"error": "Unable to load students."}), 500

    finally:
        connection.close()


# ============================================================
# UPDATE CURRENT PERFORMANCE
# ============================================================

@app.route(
    "/api/current-performance/<int:student_id>",
    methods=["POST"]
)
def update_current_performance(student_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Send performance data as JSON."
        }), 400

    values, error = validate_performance(data)

    if error:
        return jsonify({"error": error}), 400

    connection = get_connection()

    try:
        student = connection.execute("""
            SELECT id
            FROM students
            WHERE id = ?
        """, (student_id,)).fetchone()

        if student is None:
            return jsonify({"error": "Student not found."}), 404

        timestamp = current_timestamp()

        connection.execute("""
            UPDATE students
            SET attendance = ?,
                score = ?,
                study_hours = ?,
                assignment = ?,
                performance_updated_at = ?
            WHERE id = ?
        """, (
            values["attendance"],
            values["score"],
            values["study_hours"],
            values["assignment"],
            timestamp,
            student_id
        ))

        connection.execute("""
            INSERT INTO student_progress (
                student_id,
                attendance,
                score,
                study_hours,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?)
        """, (
            student_id,
            values["attendance"],
            values["score"],
            values["study_hours"],
            timestamp
        ))

        connection.commit()

        prediction = model.predict([[
            values["attendance"],
            values["score"],
            values["study_hours"]
        ]])[0]

        return jsonify({
            "message": "Student performance updated successfully.",
            "student_id": student_id,
            "predicted_risk": prediction,
            "performance": values
        })

    except Exception as error:
        connection.rollback()
        return jsonify({"error": str(error)}), 500

    finally:
        connection.close()


# ============================================================
# STUDENT PROGRESS API
# ============================================================

@app.route(
    "/api/progress/<int:student_id>",
    methods=["GET", "POST"]
)
def student_progress_api(student_id):
    connection = get_connection()

    try:
        student = connection.execute("""
            SELECT id, attendance, score, study_hours
            FROM students
            WHERE id = ?
        """, (student_id,)).fetchone()

        if student is None:
            return jsonify({"error": "Student not found."}), 404

        if request.method == "POST":
            connection.execute("""
                INSERT INTO student_progress (
                    student_id,
                    attendance,
                    score,
                    study_hours,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                student_id,
                student["attendance"] or 0,
                student["score"] or 0,
                student["study_hours"] or 0,
                current_timestamp()
            ))

            connection.commit()

        progress = connection.execute("""
            SELECT id, attendance, score,
                   study_hours, recorded_at
            FROM student_progress
            WHERE student_id = ?
            ORDER BY recorded_at DESC, id DESC
        """, (student_id,)).fetchall()

        records = [dict(row) for row in progress]

        latest = records[0] if records else None
        previous = records[1] if len(records) > 1 else None

        return jsonify({
            "student_id": student_id,
            "records": records,
            "latest": latest,
            "previous": previous,
            "progress": calculate_progress(latest, previous)
        })

    finally:
        connection.close()



# ============================================================
# INTERVENTIONS API
# ============================================================

@app.route(
    "/api/interventions/<int:student_id>",
    methods=["GET", "POST"]
)
def interventions_api(student_id):
    connection = get_connection()

    try:
        student = connection.execute("""
            SELECT id
            FROM students
            WHERE id = ?
        """, (student_id,)).fetchone()

        if student is None:
            return jsonify({"error": "Student not found."}), 404

        if request.method == "GET":
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(interventions)"
                ).fetchall()
            }

            if not columns:
                return jsonify([])

            interventions = connection.execute("""
                SELECT *
                FROM interventions
                WHERE student_id = ?
                ORDER BY id DESC
            """, (student_id,)).fetchall()

            return jsonify([
                dict(row) for row in interventions
            ])

        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "error": "Send intervention details as JSON."
            }), 400

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(interventions)"
            ).fetchall()
        }

        if not columns:
            return jsonify({
                "error": "The interventions table does not exist."
            }), 500

        available_fields = {
            "student_id",
            "recommendation",
            "description",
            "status",
            "notes",
            "intervention",
            "action"
        }

        fields = [
            field for field in available_fields
            if field in columns and field in data
        ]

        if "student_id" in columns:
            data["student_id"] = student_id
            if "student_id" not in fields:
                fields.append("student_id")

        if not fields:
            return jsonify({
                "error": "No valid intervention fields supplied."
            }), 400

        query_fields = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)

        cursor = connection.execute(
            f"INSERT INTO interventions ({query_fields}) "
            f"VALUES ({placeholders})",
            tuple(data[field] for field in fields)
        )

        connection.commit()

        return jsonify({
            "message": "Intervention created successfully.",
            "id": cursor.lastrowid
        }), 201

    except Exception as error:
        connection.rollback()
        return jsonify({"error": str(error)}), 500

    finally:
        connection.close()


@app.route(
    "/api/interventions/<int:intervention_id>",
    methods=["PUT"]
)
def update_intervention(intervention_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Send intervention changes as JSON."
        }), 400

    connection = get_connection()

    try:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(interventions)"
            ).fetchall()
        }

        if not columns:
            return jsonify({
                "error": "The interventions table does not exist."
            }), 500

        allowed_fields = {
            "recommendation",
            "description",
            "status",
            "notes",
            "intervention",
            "action"
        }

        fields = [
            field for field in data
            if field in allowed_fields and field in columns
        ]

        if not fields:
            return jsonify({
                "error": "No valid fields supplied."
            }), 400

        assignments = ", ".join(
            f"{field} = ?" for field in fields
        )

        values = [data[field] for field in fields]
        values.append(intervention_id)

        cursor = connection.execute(
            f"UPDATE interventions SET {assignments} WHERE id = ?",
            values
        )

        connection.commit()

        if cursor.rowcount == 0:
            return jsonify({
                "error": "Intervention not found."
            }), 404

        return jsonify({
            "message": "Intervention updated successfully."
        })

    except Exception as error:
        connection.rollback()
        return jsonify({"error": str(error)}), 500

    finally:
        connection.close()
        # ============================================================
# ATTENDANCE API
# ============================================================

@app.route("/api/attendance", methods=["GET", "POST"])
def attendance_api():
    connection = get_connection()

    try:
        if request.method == "GET":
            selected_date = request.args.get(
                "date", date.today().isoformat()
            )

            try:
                selected_day = date.fromisoformat(selected_date)
            except ValueError:
                return jsonify({
                    "error": "Date must use YYYY-MM-DD format."
                }), 400

            if not semester_contains(selected_day):
                return jsonify({
                    "error": "Date is outside the semester period."
                }), 400

            students = connection.execute("""
                SELECT s.id, s.name, ar.status
                FROM students s
                LEFT JOIN attendance_records ar
                    ON s.id = ar.student_id
                    AND ar.attendance_date = ?
                ORDER BY s.id
            """, (selected_date,)).fetchall()

            return jsonify({
                "date": selected_date,
                "students": [
                    {
                        "student_id": row["id"],
                        "name": row["name"],
                        "status": row["status"]
                    }
                    for row in students
                ]
            })

        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "error": "Send attendance data as JSON."
            }), 400

        selected_date = data.get("date")
        records = data.get("records")

        try:
            selected_day = date.fromisoformat(selected_date)
        except (ValueError, TypeError):
            return jsonify({
                "error": "Date must use YYYY-MM-DD format."
            }), 400

        if not semester_contains(selected_day):
            return jsonify({
                "error": "Date is outside the semester period."
            }), 400

        if selected_day > date.today():
            return jsonify({
                "error": "Future attendance cannot be recorded."
            }), 400

        if not isinstance(records, list):
            return jsonify({
                "error": "'records' must be a list."
            }), 400

        valid_statuses = {"Present", "Absent"}
        seen_ids = set()

        for record in records:
            if not isinstance(record, dict):
                return jsonify({
                    "error": "Each record must be an object."
                }), 400

            try:
                student_id = int(record["student_id"])
            except (KeyError, ValueError, TypeError):
                return jsonify({
                    "error": "Each record needs a valid student_id."
                }), 400

            if student_id in seen_ids:
                return jsonify({
                    "error": "Duplicate student IDs are not allowed."
                }), 400

            seen_ids.add(student_id)

            if record.get("status") not in valid_statuses:
                return jsonify({
                    "error": "Status must be Present or Absent."
                }), 400

        existing_ids = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM students"
            ).fetchall()
        }

        if not seen_ids.issubset(existing_ids):
            return jsonify({
                "error": "One or more students do not exist."
            }), 400

        for record in records:
            connection.execute("""
                INSERT INTO attendance_records (
                    student_id, attendance_date, status
                ) VALUES (?, ?, ?)
                ON CONFLICT(student_id, attendance_date)
                DO UPDATE SET status = excluded.status
            """, (
                int(record["student_id"]),
                selected_date,
                record["status"]
            ))

        connection.commit()

        present_count = sum(
            1 for record in records
            if record["status"] == "Present"
        )

        return jsonify({
            "message": "Attendance saved successfully.",
            "date": selected_date,
            "saved_count": len(records),
            "present_count": present_count,
            "absent_count": len(records) - present_count
        })

    except Exception as error:
        connection.rollback()
        return jsonify({"error": str(error)}), 500

    finally:
        connection.close()


# ============================================================
# SEMESTER ATTENDANCE API
# ============================================================

@app.route("/api/attendance/semester", methods=["GET"])
def semester_attendance_api():
    connection = get_connection()

    try:
        start, end = semester_dates()
        students = connection.execute("""
            SELECT id, name, attendance
            FROM students
            ORDER BY id
        """).fetchall()

        result = []

        for student in students:
            stats = semester_attendance(
                connection, student["id"]
            )

            result.append({
                "student_id": student["id"],
                "name": student["name"],
                "legacy_attendance": student["attendance"],
                "present_days": stats["present_days"],
                "recorded_days": stats["recorded_days"],
                "attendance_percentage":
                    stats["attendance_percentage"]
            })

        return jsonify({
            "semester_start": start.isoformat(),
            "semester_end": end.isoformat(),
            "students": result
        })

    finally:
        connection.close()


# ============================================================
# ATTENDANCE REPORTS API
# ============================================================

@app.route("/api/attendance/reports", methods=["GET"])
def attendance_reports_api():
    connection = get_connection()

    try:
        reports = connection.execute("""
            SELECT *
            FROM attendance_reports
            ORDER BY period_start DESC, id DESC
        """).fetchall()

        result = []

        for report in reports:
            students = connection.execute("""
                SELECT
                    ars.*,
                    s.name
                FROM attendance_report_students ars
                JOIN students s ON s.id = ars.student_id
                WHERE ars.report_id = ?
                ORDER BY s.id
            """, (report["id"],)).fetchall()

            result.append({
                "id": report["id"],
                "type": report["report_type"],
                "period_number": report["period_number"],
                "start_date": report["period_start"],
                "end_date": report["period_end"],
                "generated_at": report["generated_at"],
                "students": [
                    dict(student) for student in students
                ]
            })

        return jsonify({"reports": result})

    finally:
        connection.close()


@app.route("/api/attendance/reports/generate", methods=["POST"])
def generate_reports_api():
    generate_attendance_reports()

    return jsonify({
        "message": "Attendance report generation completed."
    })


# ============================================================
# MARKS MANAGEMENT HELPERS
# ============================================================

def valid_date_string(value):
    if not isinstance(value, str):
        return False

    try:
        parsed = date.fromisoformat(value)
        return parsed.isoformat() == value
    except ValueError:
        return False


def assessment_dict(row):
    return dict(row)


def get_assessment(connection, assessment_id):
    return connection.execute("""
        SELECT *
        FROM assessments
        WHERE id = ?
    """, (assessment_id,)).fetchone()


# ============================================================
# GET ALL ASSESSMENTS
# ============================================================

@app.route("/api/assessments", methods=["GET"])
def get_assessments():
    connection = get_connection()

    try:
        rows = connection.execute("""
            SELECT *
            FROM assessments
            ORDER BY semester, assessment_date DESC, id DESC
        """).fetchall()

        return jsonify({
            "assessments": [
                assessment_dict(row) for row in rows
            ]
        })

    finally:
        connection.close()


# ============================================================
# CREATE AN ASSESSMENT
# ============================================================

@app.route("/api/assessments", methods=["POST"])
def create_assessment():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Send assessment details as JSON."
        }), 400

    required_fields = [
        "name",
        "academic_year",
        "semester",
        "assessment_type",
        "subject",
        "max_marks",
        "assessment_date"
    ]

    missing = [
        field for field in required_fields
        if field not in data or data[field] in (None, "")
    ]

    if missing:
        return jsonify({
            "error": "Missing required fields.",
            "fields": missing
        }), 400

    name = str(data["name"]).strip()
    academic_year = str(data["academic_year"]).strip()
    subject = str(data["subject"]).strip()
    assessment_type = data["assessment_type"]

    if not name or not academic_year or not subject:
        return jsonify({
            "error": "Name, academic year, and subject cannot be empty."
        }), 400

    try:
        semester = int(data["semester"])
    except (TypeError, ValueError):
        return jsonify({
            "error": "Semester must be an integer from 1 to 8."
        }), 400

    if semester < 1 or semester > 8:
        return jsonify({
            "error": "Semester must be between 1 and 8."
        }), 400

    if assessment_type not in ("Internal", "Semester"):
        return jsonify({
            "error": "Assessment type must be Internal or Semester."
        }), 400

    try:
        max_marks = float(data["max_marks"])
    except (TypeError, ValueError):
        return jsonify({
            "error": "Maximum marks must be numeric."
        }), 400

    if not math.isfinite(max_marks) or max_marks <= 0:
        return jsonify({
            "error": "Maximum marks must be a positive number."
        }), 400

    assessment_date = data["assessment_date"]

    if not valid_date_string(assessment_date):
        return jsonify({
            "error": "Assessment date must use YYYY-MM-DD format."
        }), 400

    connection = get_connection()

    try:
        cursor = connection.execute("""
            INSERT INTO assessments (
                name,
                academic_year,
                semester,
                assessment_type,
                subject,
                max_marks,
                assessment_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            academic_year,
            semester,
            assessment_type,
            subject,
            max_marks,
            assessment_date
        ))

        connection.commit()

        created = get_assessment(
            connection, cursor.lastrowid
        )

        return jsonify({
            "message": "Assessment created successfully.",
            "assessment": assessment_dict(created)
        }), 201

    except Exception as error:
        connection.rollback()
        return jsonify({"error": str(error)}), 500

    finally:
        connection.close()


# ============================================================
# GET STUDENTS AND MARKS FOR ONE ASSESSMENT
# ============================================================

@app.route(
    "/api/assessments/<int:assessment_id>/marks",
    methods=["GET"]
)
def get_assessment_marks(assessment_id):
    connection = get_connection()

    try:
        assessment = get_assessment(
            connection, assessment_id
        )

        if assessment is None:
            return jsonify({
                "error": "Assessment not found."
            }), 404

        students = connection.execute("""
            SELECT
                s.id AS student_id,
                s.name,
                sm.marks_obtained
            FROM students s
            LEFT JOIN student_marks sm
                ON sm.student_id = s.id
                AND sm.assessment_id = ?
            ORDER BY s.id
        """, (assessment_id,)).fetchall()

        return jsonify({
            "assessment": assessment_dict(assessment),
            "students": [
                dict(student) for student in students
            ]
        })

    finally:
        connection.close()


# ============================================================
# SAVE OR UPDATE STUDENT MARKS
# ============================================================

@app.route(
    "/api/assessments/<int:assessment_id>/marks",
    methods=["POST"]
)
def save_assessment_marks(assessment_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Send marks data as JSON."
        }), 400

    records = data.get("records")

    if not isinstance(records, list):
        return jsonify({
            "error": "'records' must be a list of student marks."
        }), 400

    connection = get_connection()

    try:
        assessment = get_assessment(
            connection, assessment_id
        )

        if assessment is None:
            return jsonify({
                "error": "Assessment not found."
            }), 404

        max_marks = float(assessment["max_marks"])
        seen_ids = set()
        cleaned_records = []

        for record in records:
            if not isinstance(record, dict):
                return jsonify({
                    "error": "Each mark record must be an object."
                }), 400

            try:
                student_id = int(record["student_id"])
                marks = float(record["marks_obtained"])
            except (KeyError, TypeError, ValueError):
                return jsonify({
                    "error": (
                        "Each record needs a student_id and "
                        "numeric marks_obtained."
                    )
                }), 400

            if student_id in seen_ids:
                return jsonify({
                    "error": "Duplicate student IDs are not allowed."
                }), 400

            seen_ids.add(student_id)

            if not math.isfinite(marks):
                return jsonify({
                    "error": "Marks must be finite numbers."
                }), 400

            if marks < 0 or marks > max_marks:
                return jsonify({
                    "error": (
                        f"Marks must be between 0 and {max_marks}."
                    )
                }), 400

            cleaned_records.append((student_id, marks))

        existing_ids = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM students"
            ).fetchall()
        }

        for student_id, marks in cleaned_records:
            if student_id not in existing_ids:
                return jsonify({
                    "error": f"Student {student_id} does not exist."
                }), 400

        for student_id, marks in cleaned_records:
            existing_mark = connection.execute("""
                SELECT id
                FROM student_marks
                WHERE student_id = ?
                  AND assessment_id = ?
                ORDER BY id
            """, (
                student_id, assessment_id
            )).fetchall()

            if existing_mark:
                first_id = existing_mark[0]["id"]

                connection.execute("""
                    UPDATE student_marks
                    SET marks_obtained = ?
                    WHERE id = ?
                """, (marks, first_id))

                # Clean any duplicate legacy rows for this pair.
                for duplicate in existing_mark[1:]:
                    connection.execute("""
                        DELETE FROM student_marks
                        WHERE id = ?
                    """, (duplicate["id"],))

            else:
                connection.execute("""
                    INSERT INTO student_marks (
                        student_id,
                        assessment_id,
                        marks_obtained
                    ) VALUES (?, ?, ?)
                """, (
                    student_id,
                    assessment_id,
                    marks
                ))

        connection.commit()

        return jsonify({
            "message": "Student marks saved successfully.",
            "assessment_id": assessment_id,
            "saved_count": len(cleaned_records)
        })

    except Exception as error:
        connection.rollback()
        return jsonify({"error": str(error)}), 500

    finally:
        connection.close()


# ============================================================
# SUBJECT-WISE MARKS AND SUMMARY
# ============================================================

@app.route("/api/marks/subject-wise", methods=["GET"])
def subject_wise_marks():
    connection = get_connection()

    try:
        query = """
            SELECT
                s.id AS student_id,
                s.name AS student_name,
                a.id AS assessment_id,
                a.name AS assessment_name,
                a.academic_year,
                a.semester,
                a.assessment_type,
                a.subject,
                a.max_marks,
                a.assessment_date,
                sm.marks_obtained,
                CASE
                    WHEN sm.marks_obtained IS NOT NULL
                    THEN ROUND(
                        sm.marks_obtained * 100.0 / a.max_marks, 2
                    )
                    ELSE NULL
                END AS percentage
            FROM students s
            CROSS JOIN assessments a
            LEFT JOIN student_marks sm
                ON sm.student_id = s.id
                AND sm.assessment_id = a.id
            WHERE 1 = 1
        """

        parameters = []

        student_id = request.args.get("student_id")
        semester = request.args.get("semester")
        assessment_type = request.args.get("assessment_type")
        subject = request.args.get("subject")
        academic_year = request.args.get("academic_year")

        if student_id:
            try:
                student_id = int(student_id)
            except ValueError:
                return jsonify({
                    "error": "student_id must be an integer."
                }), 400

            query += " AND s.id = ?"
            parameters.append(student_id)

        if semester:
            try:
                semester = int(semester)
            except ValueError:
                return jsonify({
                    "error": "semester must be an integer."
                }), 400

            if not 1 <= semester <= 8:
                return jsonify({
                    "error": "semester must be between 1 and 8."
                }), 400

            query += " AND a.semester = ?"
            parameters.append(semester)

        if assessment_type:
            if assessment_type not in ("Internal", "Semester"):
                return jsonify({
                    "error": (
                        "assessment_type must be Internal or Semester."
                    )
                }), 400

            query += " AND a.assessment_type = ?"
            parameters.append(assessment_type)

        if subject:
            query += " AND LOWER(a.subject) = LOWER(?)"
            parameters.append(subject.strip())

        if academic_year:
            query += " AND a.academic_year = ?"
            parameters.append(academic_year.strip())

        query += """
            ORDER BY s.id, a.semester, a.subject, a.assessment_date
        """

        rows = connection.execute(
            query, parameters
        ).fetchall()

        results = [dict(row) for row in rows]

        # Summary for each student and subject.
        summary_query = """
            SELECT
                s.id AS student_id,
                s.name AS student_name,
                a.subject,
                a.semester,
                a.assessment_type,
                SUM(sm.marks_obtained) AS total_marks_obtained,
                SUM(a.max_marks) AS total_max_marks,
                COUNT(sm.id) AS assessments_completed,
                COUNT(a.id) AS assessments_available
            FROM students s
            CROSS JOIN assessments a
            LEFT JOIN student_marks sm
                ON sm.student_id = s.id
                AND sm.assessment_id = a.id
            WHERE sm.id IS NOT NULL
        """

        summary_parameters = []

        if student_id:
            summary_query += " AND s.id = ?"
            summary_parameters.append(student_id)

        if semester:
            summary_query += " AND a.semester = ?"
            summary_parameters.append(semester)

        if assessment_type:
            summary_query += " AND a.assessment_type = ?"
            summary_parameters.append(assessment_type)

        if subject:
            summary_query += " AND LOWER(a.subject) = LOWER(?)"
            summary_parameters.append(subject.strip())

        if academic_year:
            summary_query += " AND a.academic_year = ?"
            summary_parameters.append(academic_year.strip())

        summary_query += """
            GROUP BY
                s.id, s.name, a.subject,
                a.semester, a.assessment_type
            ORDER BY s.id, a.subject
        """

        summaries = connection.execute(
            summary_query, summary_parameters
        ).fetchall()

        subject_summary = []

        for row in summaries:
            item = dict(row)

            obtained = item["total_marks_obtained"] or 0
            maximum = item["total_max_marks"] or 0

            item["percentage"] = (
                round(obtained * 100.0 / maximum, 2)
                if maximum > 0 else None
            )

            subject_summary.append(item)

        return jsonify({
            "results": results,
            "subject_summary": subject_summary
        })

    finally:
        connection.close()


# ============================================================
# APPLICATION STARTUP
# ============================================================

scheduler = BackgroundScheduler()


def start_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        create_progress_snapshots,
        "interval",
        minutes=SNAPSHOT_INTERVAL_MINUTES,
        id="progress_snapshots",
        replace_existing=True,
        max_instances=1
    )

    scheduler.add_job(
        generate_attendance_reports,
        "interval",
        seconds=SCHEDULER_INTERVAL_SECONDS,
        id="attendance_reports",
        replace_existing=True,
        max_instances=1
    )

    scheduler.start()



@app.route("/api/placement/<int:student_id>", methods=["GET"])
def get_placement(student_id):
    connection = get_connection()

    try:
        student = connection.execute(
            "SELECT id FROM students WHERE id = ?",
            (student_id,)
        ).fetchone()

        if not student:
            return {"error": "Student not found"}, 404

        placement = connection.execute(
            "SELECT * FROM placement_records WHERE student_id = ?",
            (student_id,)
        ).fetchone()

        if not placement:
            return {
                "student_id": student_id,
                "aptitude_score": 0,
                "coding_score": 0,
                "communication_score": 0,
                "mock_interview_score": 0,
                "placement_status": "Not Placed",
                "company_name": None
            }, 200

        return dict(placement), 200

    finally:
        connection.close()


@app.route("/api/placement/<int:student_id>", methods=["POST"])
def save_placement(student_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return {"error": "Send placement data as JSON"}, 400

    score_fields = [
        "aptitude_score",
        "coding_score",
        "communication_score",
        "mock_interview_score"
    ]

    scores = {}

    for field in score_fields:
        value = data.get(field, 0)

        if isinstance(value, bool):
            return {"error": f"{field} must be between 0 and 100"}, 400

        try:
            value = float(value)
        except (TypeError, ValueError):
            return {"error": f"{field} must be a number"}, 400

        if not math.isfinite(value) or not 0 <= value <= 100:
            return {"error": f"{field} must be between 0 and 100"}, 400

        scores[field] = value

    status = data.get("placement_status", "Not Placed")
    allowed_statuses = ["Not Placed", "Placed", "In Progress"]

    if status not in allowed_statuses:
        return {"error": "Invalid placement status"}, 400

    company_name = data.get("company_name")

    if company_name is not None and not isinstance(company_name, str):
        return {"error": "company_name must be text"}, 400

    if company_name:
        company_name = company_name.strip() or None

    connection = get_connection()

    try:
        student = connection.execute(
            "SELECT id FROM students WHERE id = ?",
            (student_id,)
        ).fetchone()

        if not student:
            return {"error": "Student not found"}, 404

        connection.execute("""
            INSERT INTO placement_records (
                student_id,
                aptitude_score,
                coding_score,
                communication_score,
                mock_interview_score,
                placement_status,
                company_name,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(student_id) DO UPDATE SET
                aptitude_score = excluded.aptitude_score,
                coding_score = excluded.coding_score,
                communication_score = excluded.communication_score,
                mock_interview_score = excluded.mock_interview_score,
                placement_status = excluded.placement_status,
                company_name = excluded.company_name,
                updated_at = CURRENT_TIMESTAMP
        """, (
            student_id,
            scores["aptitude_score"],
            scores["coding_score"],
            scores["communication_score"],
            scores["mock_interview_score"],
            status,
            company_name
        ))

        connection.commit()

        return {
            "message": "Placement record saved successfully",
            "student_id": student_id
        }, 200

    except Exception:
        connection.rollback()
        app.logger.exception("Failed to save placement record")
        return {"error": "Could not save placement record"}, 500

    finally:
        connection.close()
    print("PLACEMENT ROUTES:")
for route in app.url_map.iter_rules():
    if "placement" in route.rule:
        print(route, route.methods)

@app.route("/placement")
@login_required
@role_required("admin", "staff")
def placement_page():

    connection = get_connection()
    try:
        students = connection.execute(
            "SELECT id, name FROM students ORDER BY id"
        ).fetchall()

        return render_template(
            "placement.html",
            students=students
        )
    finally:
        connection.close()

initialize_database()
if __name__ == "__main__":
    

    connection = get_connection()
    admin_exists = connection.execute(
        "SELECT id FROM user_accounts WHERE role = 'admin' LIMIT 1"
    ).fetchone()
    connection.close()

    if not admin_exists:
        print("\nCreate your first admin account")
        admin_username = input("Admin username: ").strip()
        admin_password = getpass("Admin password: ")

        if admin_username and admin_password:
            create_admin_account(admin_username, admin_password)
        else:
            print("Username and password cannot be empty.")
            exit()

    create_progress_snapshots()
    generate_attendance_reports()
    start_scheduler()

    try:
        app.run(debug=True, use_reloader=False)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)