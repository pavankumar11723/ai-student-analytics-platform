import sqlite3


# ==================================================
# CONNECT TO DATABASE
# ==================================================

connection = sqlite3.connect("students.db")

cursor = connection.cursor()


# ==================================================
# ADD STUDY HOURS COLUMN
# ==================================================

try:

    cursor.execute("""
        ALTER TABLE students
        ADD COLUMN study_hours INTEGER DEFAULT 2
    """)

    print(
        "Study hours column added successfully."
    )

except sqlite3.OperationalError:

    print(
        "Study hours column already exists."
    )


# ==================================================
# UPDATE STUDY HOURS
# ==================================================

cursor.execute("""
    UPDATE students
    SET study_hours = 3
    WHERE name = 'Rahul'
""")


cursor.execute("""
    UPDATE students
    SET study_hours = 4
    WHERE name = 'Anjali'
""")


cursor.execute("""
    UPDATE students
    SET study_hours = 6
    WHERE name = 'Arjun'
""")


cursor.execute("""
    UPDATE students
    SET study_hours = 2
    WHERE name = 'Priya'
""")


cursor.execute("""
    UPDATE students
    SET study_hours = 5
    WHERE name = 'Kiran'
""")


# ==================================================
# CREATE INTERVENTIONS TABLE
# ==================================================

cursor.execute("""
    CREATE TABLE IF NOT EXISTS interventions (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        student_id INTEGER NOT NULL,

        action TEXT NOT NULL,

        status TEXT DEFAULT 'Pending',

        notes TEXT,

        created_at
            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        FOREIGN KEY (student_id)
            REFERENCES students(id)

    )
""")


# ==================================================
# SAVE CHANGES
# ==================================================

connection.commit()

connection.close()


print(
    "Database updated successfully."
)
