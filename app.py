"""Student Manager: a small student information system for the Azure App Service lab (Part II).

Everything the platform needs to know is set in the portal, under the web
app's Environment variables (app settings). This file only contains
application logic:

  * Students: list with search and filters, profile page, add, edit, delete
  * Courses: list, course page with its roster, add, edit, delete
  * Enrollments and grades, with a credit-weighted GPA for every student
  * A dashboard, switched on by the FEATURE_DASHBOARD setting (Task 6)
  * A Platform page that shows what App Service is doing for the app

App Service starts this file automatically: it looks for app.py with a
Flask object named `app` and runs it with Gunicorn.
"""

import logging
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError
from flask import Flask, abort, flash, redirect, render_template, request, url_for

from seed_data import SEED_COURSES, SEED_STUDENTS


def env_flag(name):
    """True when an app setting is set to true, 1, yes or on."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


# --- Settings (App Service -> Settings -> Environment variables) ------------

APP_TITLE = os.environ.get("APP_TITLE", "Student Manager")
APP_COLOR = os.environ.get("APP_COLOR", "#2563eb")
APP_VERSION = os.environ.get("APP_VERSION", "v1")
FEATURE_DASHBOARD = env_flag("FEATURE_DASHBOARD")
COSMOS_CONNECTION_STRING = os.environ.get("COSMOS_CONNECTION_STRING", "")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "students-db")
COSMOS_CONTAINER = os.environ.get("COSMOS_CONTAINER", "students")

# App Service sets these automatically. Locally they fall back to "local".
INSTANCE_ID = os.environ.get("WEBSITE_INSTANCE_ID", "local")
REGION = os.environ.get("REGION_NAME", "local")
SITE_NAME = os.environ.get("WEBSITE_SITE_NAME", "local")
PLAN_TIER = os.environ.get("WEBSITE_SKU", "local")
HOSTNAME = os.environ.get("WEBSITE_HOSTNAME", "localhost")

# The shared database throughput, in request units per second. The Cosmos DB
# free tier covers 1000 RU/s, so 400 stays well inside it.
DATABASE_THROUGHPUT = 400

# Bump the number when the sample data changes: the next start loads the new set.
SEED_MARKER_ID = "_lab_seed_marker_v2"

MAJORS = (
    "Computer Science",
    "Artificial Intelligence",
    "Software Engineering",
    "Data Science",
    "Cybersecurity",
    "Business Administration",
)
GRADE_POINTS = {"A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7, "C+": 2.3,
                "C": 2.0, "C-": 1.7, "D": 1.0, "F": 0.0}
GRADES = (*GRADE_POINTS, "IP")  # IP = in progress, not counted in the GPA
STUDENT_FIELDS = ("student_id", "name", "major", "year", "email")
COURSE_FIELDS = ("code", "title", "credits", "instructor")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("student-manager")
# The Cosmos DB library logs every HTTP call; keep only its warnings.
logging.getLogger("azure").setLevel(logging.WARNING)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-for-anything-real")

# Shown on the Platform page: when this process started and how much it has done.
STARTED_AT = time.time()
_request_count = 0
_count_lock = threading.Lock()


# --- Cosmos DB ------------------------------------------------------------------

_container = None
_container_lock = threading.Lock()


def get_container():
    """Connect to Cosmos DB on first use, creating the database and container if needed.

    The connection string comes from an app setting, never from the code.
    """
    global _container
    if _container is not None:
        return _container
    with _container_lock:
        if _container is None:
            if not COSMOS_CONNECTION_STRING:
                raise RuntimeError(
                    "COSMOS_CONNECTION_STRING is not set. Add it under the web app's "
                    "Environment variables (Task 4)."
                )
            client = CosmosClient.from_connection_string(COSMOS_CONNECTION_STRING)
            database = client.create_database_if_not_exists(
                id=COSMOS_DATABASE, offer_throughput=DATABASE_THROUGHPUT
            )
            _container = database.create_container_if_not_exists(
                id=COSMOS_CONTAINER, partition_key=PartitionKey(path="/id")
            )
            log.info("CONNECTED to database '%s', container '%s'", COSMOS_DATABASE, COSMOS_CONTAINER)
    return _container


# Students, courses and the seed marker share one container. Each item has a
# "type" field ("student", "course" or "meta") that says what it is.

def list_items(kind):
    """Return every item of one type."""
    return list(get_container().query_items(
        query="SELECT * FROM c WHERE c.type = @type",
        parameters=[{"name": "@type", "value": kind}],
        enable_cross_partition_query=True,
    ))


def get_item(doc_id, kind):
    """Return one item of the given type, or None if it doesn't exist."""
    try:
        item = get_container().read_item(item=doc_id, partition_key=doc_id)
    except CosmosResourceNotFoundError:
        return None
    return item if item.get("type") == kind else None


def save_item(item):
    """Create or replace an item, dropping Cosmos DB's system fields (_rid, _etag, ...)."""
    get_container().upsert_item({k: v for k, v in item.items() if not k.startswith("_")})


def remove_item(doc_id):
    get_container().delete_item(item=doc_id, partition_key=doc_id)


def new_id():
    return uuid.uuid4().hex[:20]


def write_seed_data():
    """Write the sample courses and students. Fixed IDs make this safe to repeat."""
    for course in SEED_COURSES:
        save_item({"type": "course", **course})
    for i, student in enumerate(SEED_STUDENTS, start=1):
        save_item({"id": f"seed-{i:02d}", "type": "student", **student})


def delete_all(kind):
    """Delete every item of one type and return how many were removed."""
    items = list_items(kind)
    for item in items:
        remove_item(item["id"])
    return len(items)


# --- Seed on first run --------------------------------------------------------
# The first time this version of the app runs, it creates a marker item and
# loads the sample data. create_item() fails if the marker already exists, so
# the data is loaded only once: two instances starting together cannot both
# seed, and records someone deleted are never quietly brought back by a restart.

_seed_checked = False
_seed_lock = threading.Lock()


def seed_on_first_run():
    global _seed_checked
    if _seed_checked:
        return
    with _seed_lock:
        if _seed_checked:
            return
        try:
            get_container().create_item({"id": SEED_MARKER_ID, "type": "meta", "seeded_by_version": APP_VERSION})
        except CosmosResourceExistsError:
            log.info("SEED skipped: database was already seeded")
        else:
            write_seed_data()
            log.info("SEED loaded %d courses and %d students", len(SEED_COURSES), len(SEED_STUDENTS))
        _seed_checked = True


@app.before_request
def before_each_request():
    global _request_count
    if request.endpoint in ("static", None):
        return
    with _count_lock:
        _request_count += 1
    if request.endpoint != "platform":
        seed_on_first_run()


# --- Grades and GPA -------------------------------------------------------------

def gpa_for(enrollments, courses_by_id):
    """Credit-weighted GPA on a 4.0 scale. Courses in progress don't count."""
    points = credits = 0
    for enrollment in enrollments:
        course = courses_by_id.get(enrollment.get("course_id"))
        grade = enrollment.get("grade")
        if course is None or grade not in GRADE_POINTS:
            continue
        points += GRADE_POINTS[grade] * course["credits"]
        credits += course["credits"]
    return round(points / credits, 2) if credits else None


def student_summary(item, courses_by_id):
    """The fields the pages need for one student, including course count and GPA."""
    enrollments = [e for e in item.get("enrollments", []) if e.get("course_id") in courses_by_id]
    return {
        "id": item["id"],
        **{field: item.get(field) for field in STUDENT_FIELDS},
        "course_count": len(enrollments),
        "gpa": gpa_for(enrollments, courses_by_id),
    }


def load_catalog():
    """All courses (by ID) and all students, read once per page."""
    courses = {c["id"]: c for c in list_items("course")}
    students = list_items("student")
    return courses, students


# --- Forms ----------------------------------------------------------------------

def read_student_form(exclude_id=None):
    """Return (data, errors) from the submitted student form."""
    data = {field: request.form.get(field, "").strip() for field in STUDENT_FIELDS}
    errors = {}
    if not data["student_id"]:
        errors["student_id"] = "Enter a student ID, for example S1013."
    elif any(s.get("student_id", "").lower() == data["student_id"].lower() and s["id"] != exclude_id
             for s in list_items("student")):
        errors["student_id"] = "Another student already has this ID."
    if not data["name"]:
        errors["name"] = "Enter the student's full name."
    if data["major"] not in MAJORS:
        errors["major"] = "Choose a major from the list."
    if data["year"] not in {"1", "2", "3", "4", "5"}:
        errors["year"] = "Choose a year from 1 to 5."
    if not EMAIL_RE.match(data["email"]):
        errors["email"] = "Enter an email address like name@example.edu."
    if not errors:
        data["year"] = int(data["year"])
    return data, errors


def read_course_form(exclude_id=None):
    """Return (data, errors) from the submitted course form."""
    data = {field: request.form.get(field, "").strip() for field in COURSE_FIELDS}
    data["code"] = re.sub(r"\s+", " ", data["code"]).upper()
    errors = {}
    if not data["code"]:
        errors["code"] = "Enter a course code, for example CS 420."
    elif any(c.get("code", "").upper() == data["code"] and c["id"] != exclude_id for c in list_items("course")):
        errors["code"] = "Another course already uses this code."
    if not data["title"]:
        errors["title"] = "Enter the course title."
    if data["credits"] not in {"1", "2", "3", "4", "5", "6"}:
        errors["credits"] = "Choose between 1 and 6 credits."
    if not data["instructor"]:
        errors["instructor"] = "Enter the instructor's name."
    if not errors:
        data["credits"] = int(data["credits"])
    return data, errors


# --- Page helpers ---------------------------------------------------------------

def text_color_for(hex_color):
    """Pick dark or white text so the header stays readable with any APP_COLOR."""
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return "#ffffff"

    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return "#16202b" if luminance > 0.4 else "#ffffff"


@app.template_filter("gpa")
def format_gpa(value):
    return "—" if value is None else f"{value:.2f}"


@app.template_filter("grade")
def format_grade(value):
    return "In progress" if value == "IP" else value


@app.context_processor
def inject_platform_info():
    return {
        "app_title": APP_TITLE,
        "app_color": APP_COLOR,
        "app_text_color": text_color_for(APP_COLOR),
        "app_version": APP_VERSION,
        "instance_id": INSTANCE_ID,
        "dashboard_enabled": FEATURE_DASHBOARD,
        "majors": MAJORS,
        "grades": GRADES,
    }


# --- Home and dashboard ---------------------------------------------------------

@app.get("/")
def home():
    """The dashboard when FEATURE_DASHBOARD is on, otherwise the student list."""
    if not FEATURE_DASHBOARD:
        return redirect(url_for("list_students"))
    courses, students = load_catalog()
    summaries = [student_summary(s, courses) for s in students]
    gpas = [s["gpa"] for s in summaries if s["gpa"] is not None]

    by_major = {major: 0 for major in MAJORS}
    for s in summaries:
        by_major[s["major"]] = by_major.get(s["major"], 0) + 1
    most = max(by_major.values(), default=0) or 1
    majors = [{"name": m, "count": n, "width": round(100 * n / most)}
              for m, n in sorted(by_major.items(), key=lambda kv: (-kv[1], kv[0]))]

    enrolled = {cid: 0 for cid in courses}
    grade_counts = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0, "IP": 0}
    for s in students:
        for e in s.get("enrollments", []):
            if e.get("course_id") in courses:
                enrolled[e["course_id"]] += 1
                grade = e.get("grade", "IP")
                grade_counts["IP" if grade == "IP" else grade[0]] += 1
    top_courses = sorted(courses.values(), key=lambda c: (-enrolled[c["id"]], c["code"]))[:5]
    busiest = max(enrolled.values(), default=0) or 1
    top_courses = [{**c, "enrolled": enrolled[c["id"]], "width": round(100 * enrolled[c["id"]] / busiest)}
                   for c in top_courses]
    most_grades = max(grade_counts.values(), default=0) or 1
    grade_bars = [{"label": "In progress" if g == "IP" else g, "count": n, "width": round(100 * n / most_grades)}
                  for g, n in grade_counts.items()]
    top_students = sorted((s for s in summaries if s["gpa"] is not None), key=lambda s: (-s["gpa"], s["name"]))[:5]

    log.info("READ dashboard: %d students, %d courses", len(students), len(courses))
    return render_template(
        "dashboard.html",
        stats={
            "students": len(students),
            "courses": len(courses),
            "enrollments": sum(enrolled.values()),
            "average_gpa": round(sum(gpas) / len(gpas), 2) if gpas else None,
        },
        majors=majors, top_courses=top_courses, grade_bars=grade_bars, top_students=top_students,
    )


# --- Students -------------------------------------------------------------------

@app.get("/students")
def list_students():
    """READ: all students, with optional search and filters."""
    query = request.args.get("q", "").strip()
    major = request.args.get("major", "")
    year = request.args.get("year", "")
    courses, students = load_catalog()
    summaries = [student_summary(s, courses) for s in students]
    total = len(summaries)
    if query:
        q = query.lower()
        summaries = [s for s in summaries
                     if q in s["name"].lower() or q in s["student_id"].lower() or q in s["email"].lower()]
    if major:
        summaries = [s for s in summaries if s["major"] == major]
    if year:
        summaries = [s for s in summaries if str(s["year"]) == year]
    summaries.sort(key=lambda s: s["name"].lower())
    log.info("READ %d of %d students", len(summaries), total)
    return render_template("students.html", students=summaries, total=total,
                           filters={"q": query, "major": major, "year": year})


@app.get("/students/<doc_id>")
def view_student(doc_id):
    """READ: one student's profile, courses, grades and GPA."""
    item = get_item(doc_id, "student")
    if item is None:
        abort(404)
    courses = {c["id"]: c for c in list_items("course")}
    rows = []
    for e in item.get("enrollments", []):
        course = courses.get(e.get("course_id"))
        if course:
            rows.append({**course, "grade": e.get("grade", "IP")})
    rows.sort(key=lambda r: r["code"])
    enrolled_ids = {r["id"] for r in rows}
    available = sorted((c for c in courses.values() if c["id"] not in enrolled_ids), key=lambda c: c["code"])
    return render_template(
        "student_detail.html",
        student=student_summary(item, courses),
        enrollments=rows,
        available_courses=available,
        credits_completed=sum(r["credits"] for r in rows if r["grade"] in GRADE_POINTS),
        credits_in_progress=sum(r["credits"] for r in rows if r["grade"] == "IP"),
    )


@app.route("/students/new", methods=["GET", "POST"])
def add_student():
    """CREATE: add a new student."""
    if request.method == "POST":
        data, errors = read_student_form()
        if errors:
            return render_template("student_form.html", mode="add", student=data, errors=errors), 400
        doc_id = new_id()
        save_item({"id": doc_id, "type": "student", **data, "enrollments": []})
        log.info("CREATE student %s (%s) as item %s", data["student_id"], data["name"], doc_id)
        flash(f"Added {data['name']}.")
        return redirect(url_for("view_student", doc_id=doc_id))
    return render_template("student_form.html", mode="add", student={}, errors={})


@app.route("/students/<doc_id>/edit", methods=["GET", "POST"])
def edit_student(doc_id):
    """UPDATE: change a student's details. Their courses and grades are kept."""
    item = get_item(doc_id, "student")
    if item is None:
        abort(404)
    if request.method == "POST":
        data, errors = read_student_form(exclude_id=doc_id)
        if errors:
            return render_template("student_form.html", mode="edit", student={"id": doc_id, **data}, errors=errors), 400
        save_item({**item, **data})
        log.info("UPDATE student %s (%s), item %s", data["student_id"], data["name"], doc_id)
        flash(f"Saved changes to {data['name']}.")
        return redirect(url_for("view_student", doc_id=doc_id))
    return render_template("student_form.html", mode="edit",
                           student={"id": doc_id, **{f: item.get(f) for f in STUDENT_FIELDS}}, errors={})


@app.post("/students/<doc_id>/delete")
def delete_student(doc_id):
    """DELETE: remove a student, with their enrollments."""
    item = get_item(doc_id, "student")
    if item is None:
        abort(404)
    remove_item(doc_id)
    log.info("DELETE student %s, item %s", item.get("name", doc_id), doc_id)
    flash(f"Deleted {item.get('name', doc_id)}.")
    return redirect(url_for("list_students"))


# --- Enrollments ----------------------------------------------------------------

@app.post("/students/<doc_id>/enrollments")
def add_enrollment(doc_id):
    """Enroll a student in a course, with an optional grade."""
    item = get_item(doc_id, "student")
    if item is None:
        abort(404)
    course = get_item(request.form.get("course_id", ""), "course")
    grade = request.form.get("grade", "IP")
    if course is None or grade not in GRADES:
        flash("Choose a course and a grade.", "error")
        return redirect(url_for("view_student", doc_id=doc_id))
    enrollments = item.get("enrollments", [])
    if any(e.get("course_id") == course["id"] for e in enrollments):
        flash(f"{item['name']} is already enrolled in {course['code']}.", "error")
        return redirect(url_for("view_student", doc_id=doc_id))
    item["enrollments"] = [*enrollments, {"course_id": course["id"], "grade": grade}]
    save_item(item)
    log.info("ENROLL student %s in %s with grade %s", item["name"], course["code"], grade)
    flash(f"Enrolled {item['name']} in {course['code']}.")
    return redirect(url_for("view_student", doc_id=doc_id))


@app.post("/students/<doc_id>/enrollments/<course_id>")
def update_enrollment(doc_id, course_id):
    """Change the grade for one of a student's courses."""
    item = get_item(doc_id, "student")
    grade = request.form.get("grade", "")
    if item is None or grade not in GRADES:
        abort(404)
    for e in item.get("enrollments", []):
        if e.get("course_id") == course_id:
            e["grade"] = grade
            break
    else:
        abort(404)
    save_item(item)
    course = get_item(course_id, "course") or {"code": course_id}
    log.info("GRADE student %s in %s set to %s", item["name"], course["code"], grade)
    flash(f"Updated the grade for {course['code']}.")
    return redirect(url_for("view_student", doc_id=doc_id))


@app.post("/students/<doc_id>/enrollments/<course_id>/delete")
def remove_enrollment(doc_id, course_id):
    """Drop a course from a student's record."""
    item = get_item(doc_id, "student")
    if item is None:
        abort(404)
    before = len(item.get("enrollments", []))
    item["enrollments"] = [e for e in item.get("enrollments", []) if e.get("course_id") != course_id]
    if len(item["enrollments"]) == before:
        abort(404)
    save_item(item)
    course = get_item(course_id, "course") or {"code": course_id}
    log.info("DROP student %s from %s", item["name"], course["code"])
    flash(f"Removed {course['code']} from {item['name']}'s courses.")
    return redirect(url_for("view_student", doc_id=doc_id))


# --- Courses --------------------------------------------------------------------

@app.get("/courses")
def list_courses():
    """READ: all courses with how many students take each."""
    courses, students = load_catalog()
    rows = []
    for course in courses.values():
        grades = [e.get("grade") for s in students for e in s.get("enrollments", [])
                  if e.get("course_id") == course["id"]]
        graded = [GRADE_POINTS[g] for g in grades if g in GRADE_POINTS]
        rows.append({**course, "enrolled": len(grades),
                     "average": round(sum(graded) / len(graded), 2) if graded else None})
    rows.sort(key=lambda c: c["code"])
    log.info("READ %d courses", len(rows))
    return render_template("courses.html", courses=rows)


@app.get("/courses/<doc_id>")
def view_course(doc_id):
    """READ: one course and the students enrolled in it."""
    course = get_item(doc_id, "course")
    if course is None:
        abort(404)
    roster = []
    for s in list_items("student"):
        for e in s.get("enrollments", []):
            if e.get("course_id") == doc_id:
                roster.append({"id": s["id"], "name": s["name"], "student_id": s["student_id"],
                               "major": s["major"], "grade": e.get("grade", "IP")})
    roster.sort(key=lambda r: r["name"].lower())
    graded = [GRADE_POINTS[r["grade"]] for r in roster if r["grade"] in GRADE_POINTS]
    return render_template("course_detail.html", course=course, roster=roster,
                           average=round(sum(graded) / len(graded), 2) if graded else None)


@app.route("/courses/new", methods=["GET", "POST"])
def add_course():
    """CREATE: add a new course."""
    if request.method == "POST":
        data, errors = read_course_form()
        if errors:
            return render_template("course_form.html", mode="add", course=data, errors=errors), 400
        doc_id = new_id()
        save_item({"id": doc_id, "type": "course", **data})
        log.info("CREATE course %s (%s) as item %s", data["code"], data["title"], doc_id)
        flash(f"Added {data['code']}.")
        return redirect(url_for("view_course", doc_id=doc_id))
    return render_template("course_form.html", mode="add", course={}, errors={})


@app.route("/courses/<doc_id>/edit", methods=["GET", "POST"])
def edit_course(doc_id):
    """UPDATE: change a course's details."""
    item = get_item(doc_id, "course")
    if item is None:
        abort(404)
    if request.method == "POST":
        data, errors = read_course_form(exclude_id=doc_id)
        if errors:
            return render_template("course_form.html", mode="edit", course={"id": doc_id, **data}, errors=errors), 400
        save_item({**item, **data})
        log.info("UPDATE course %s (%s), item %s", data["code"], data["title"], doc_id)
        flash(f"Saved changes to {data['code']}.")
        return redirect(url_for("view_course", doc_id=doc_id))
    return render_template("course_form.html", mode="edit",
                           course={"id": doc_id, **{f: item.get(f) for f in COURSE_FIELDS}}, errors={})


@app.post("/courses/<doc_id>/delete")
def delete_course(doc_id):
    """DELETE: remove a course and drop it from every student's record."""
    item = get_item(doc_id, "course")
    if item is None:
        abort(404)
    dropped = 0
    for s in list_items("student"):
        kept = [e for e in s.get("enrollments", []) if e.get("course_id") != doc_id]
        if len(kept) != len(s.get("enrollments", [])):
            s["enrollments"] = kept
            save_item(s)
            dropped += 1
    remove_item(doc_id)
    log.info("DELETE course %s, item %s, dropped from %d students", item.get("code", doc_id), doc_id, dropped)
    flash(f"Deleted {item.get('code', doc_id)} and removed it from {dropped} student records.")
    return redirect(url_for("list_courses"))


# --- Platform -------------------------------------------------------------------

@app.get("/platform")
def platform():
    """What App Service is doing for this app, as seen from inside the app."""
    uptime = int(time.time() - STARTED_AT)
    hours, rest = divmod(uptime, 3600)
    minutes, seconds = divmod(rest, 60)
    database = {"status": "Connected", "latency_ms": None, "error": None}
    try:
        start = time.perf_counter()
        get_item(SEED_MARKER_ID, "meta")
        database["latency_ms"] = round((time.perf_counter() - start) * 1000)
    except Exception as exc:  # shown on the page so students can see what failed
        database = {"status": "Not reachable", "latency_ms": None, "error": str(exc)[:200]}
    return render_template(
        "platform.html",
        info={
            "version": APP_VERSION,
            "instance": INSTANCE_ID,
            "site": SITE_NAME,
            "hostname": HOSTNAME,
            "region": REGION,
            "plan": PLAN_TIER,
            "python": sys.version.split()[0],
            "started": datetime.fromtimestamp(STARTED_AT, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "uptime": f"{hours}h {minutes:02d}m {seconds:02d}s",
            "requests": _request_count,
            "process": os.getpid(),
        },
        database=database,
        settings={
            "APP_VERSION": APP_VERSION,
            "APP_COLOR": APP_COLOR,
            "FEATURE_DASHBOARD": "on" if FEATURE_DASHBOARD else "off",
            "COSMOS_DATABASE": COSMOS_DATABASE,
            "COSMOS_CONTAINER": COSMOS_CONTAINER,
            "COSMOS_CONNECTION_STRING": "set (hidden)" if COSMOS_CONNECTION_STRING else "missing",
        },
    )


# --- Reset and errors -----------------------------------------------------------

@app.post("/reset")
def reset_data():
    """Delete every student and course and load the sample data again."""
    removed_students = delete_all("student")
    removed_courses = delete_all("course")
    write_seed_data()
    log.info("RESET removed %d students and %d courses, loaded the sample data",
             removed_students, removed_courses)
    flash(f"Reset the demo data: {len(SEED_STUDENTS)} students and {len(SEED_COURSES)} courses are back.")
    return redirect(url_for("list_students"))


@app.errorhandler(404)
def not_found(_error):
    return render_template("not_found.html"), 404


if __name__ == "__main__":
    # Local run only. On App Service, Gunicorn starts the app.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), debug=True)
