"""Sample data loaded the first time the app runs, and by "Reset demo data".

Courses and students use fixed IDs, so loading the samples twice is harmless.
Each student keeps its enrollments inside its own item: in a document
database, data that is read together is usually stored together.
"""

SEED_COURSES = [
    {"id": "course-01", "code": "CS 101", "title": "Introduction to Programming", "credits": 3, "instructor": "Dr. Amina Ouazzani"},
    {"id": "course-02", "code": "CS 210", "title": "Data Structures", "credits": 3, "instructor": "Dr. Karim Benali"},
    {"id": "course-03", "code": "CS 351", "title": "Cloud Computing", "credits": 3, "instructor": "Dr. Leila Haddad"},
    {"id": "course-04", "code": "AI 330", "title": "Machine Learning", "credits": 4, "instructor": "Dr. Nadia Cherkaoui"},
    {"id": "course-05", "code": "DS 240", "title": "Databases", "credits": 3, "instructor": "Dr. Omar El Fassi"},
    {"id": "course-06", "code": "BA 120", "title": "Principles of Management", "credits": 2, "instructor": "Dr. Rachid Tahiri"},
    {"id": "course-07", "code": "CY 310", "title": "Network Security", "credits": 3, "instructor": "Dr. Hind Zerouali"},
]


def _enroll(*pairs):
    return [{"course_id": course_id, "grade": grade} for course_id, grade in pairs]


SEED_STUDENTS = [
    {"student_id": "S1001", "name": "Ali Benjelloun", "major": "Computer Science", "year": 3, "email": "a.benjelloun@example.edu",
     "enrollments": _enroll(("course-01", "A"), ("course-02", "B+"), ("course-03", "IP"))},
    {"student_id": "S1002", "name": "Sara El Amrani", "major": "Artificial Intelligence", "year": 2, "email": "s.elamrani@example.edu",
     "enrollments": _enroll(("course-01", "A-"), ("course-04", "IP"))},
    {"student_id": "S1003", "name": "Omar Tazi", "major": "Software Engineering", "year": 4, "email": "o.tazi@example.edu",
     "enrollments": _enroll(("course-01", "B"), ("course-02", "A-"), ("course-03", "A"), ("course-05", "B+"))},
    {"student_id": "S1004", "name": "Imane Berrada", "major": "Data Science", "year": 1, "email": "i.berrada@example.edu",
     "enrollments": _enroll(("course-01", "B+"))},
    {"student_id": "S1005", "name": "Youssef Alaoui", "major": "Cybersecurity", "year": 3, "email": "y.alaoui@example.edu",
     "enrollments": _enroll(("course-01", "B-"), ("course-07", "A"), ("course-03", "IP"))},
    {"student_id": "S1006", "name": "Salma Idrissi", "major": "Business Administration", "year": 2, "email": "s.idrissi@example.edu",
     "enrollments": _enroll(("course-06", "A"), ("course-05", "B"))},
    {"student_id": "S1007", "name": "Mehdi Chraibi", "major": "Computer Science", "year": 1, "email": "m.chraibi@example.edu",
     "enrollments": _enroll(("course-01", "IP"))},
    {"student_id": "S1008", "name": "Nour Lahlou", "major": "Artificial Intelligence", "year": 4, "email": "n.lahlou@example.edu",
     "enrollments": _enroll(("course-02", "A"), ("course-04", "A-"), ("course-05", "A"), ("course-03", "B+"))},
    {"student_id": "S1009", "name": "Hamza Fassi", "major": "Software Engineering", "year": 2, "email": "h.fassi@example.edu",
     "enrollments": _enroll(("course-01", "C+"), ("course-02", "B-"))},
    {"student_id": "S1010", "name": "Kenza Sqalli", "major": "Data Science", "year": 3, "email": "k.sqalli@example.edu",
     "enrollments": _enroll(("course-05", "A-"), ("course-04", "B+"), ("course-03", "IP"))},
    {"student_id": "S1011", "name": "Rayan Bennani", "major": "Cybersecurity", "year": 5, "email": "r.bennani@example.edu",
     "enrollments": _enroll(("course-07", "A-"), ("course-02", "B"), ("course-03", "A-"), ("course-06", "B+"))},
    {"student_id": "S1012", "name": "Lina Kettani", "major": "Computer Science", "year": 2, "email": "l.kettani@example.edu",
     "enrollments": _enroll(("course-01", "A"), ("course-02", "B+"))},
]
