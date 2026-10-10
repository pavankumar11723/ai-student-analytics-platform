from sklearn.tree import DecisionTreeClassifier
import sqlite3


# =========================
# TRAINING DATA
# =========================

# Features:
# [attendance, score, study_hours]

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
    [95, 92, 7]

]


# =========================
# RISK LABELS
# =========================

y = [

    "High",
    "High",
    "High",
    "High",
    "High",

    "Medium",
    "Medium",
    "Medium",

    "Low",
    "Low",
    "Low",
    "Low"

]


# =========================
# CREATE MODEL
# =========================

model = DecisionTreeClassifier(
    random_state=42
)


# =========================
# TRAIN MODEL
# =========================

model.fit(X, y)


# =========================
# CONNECT DATABASE
# =========================

connection = sqlite3.connect("students.db")

cursor = connection.cursor()


# =========================
# GET STUDENTS
# =========================

cursor.execute("""
    SELECT id, name, attendance, score, study_hours
    FROM students
""")


students = cursor.fetchall()


# =========================
# PREDICT RISK
# =========================

for student in students:

    student_id = student[0]

    name = student[1]

    attendance = student[2]

    score = student[3]

    study_hours = student[4]


    prediction = model.predict([
        [
            attendance,
            score,
            study_hours
        ]
    ])


    risk = prediction[0]


    print(
        f"{name} | "
        f"Attendance: {attendance}% | "
        f"Score: {score}% | "
        f"Study Hours: {study_hours} | "
        f"Predicted Risk: {risk}"
    )


# =========================
# CLOSE DATABASE
# =========================

connection.close()

