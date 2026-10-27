"""Student Manager: a small CRUD app for the Azure App Service + Cosmos DB lab (Part II).

Everything the platform needs to know is set in the portal, under the web
app's Environment variables (app settings). This file only contains
application logic: list, add, edit and delete students, plus a button that
restores the sample data.

App Service starts this file automatically: it looks for app.py with a
Flask object named `app` and runs it with Gunicorn.
"""

import logging
import os
import re
import threading
import uuid

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError
from flask import Flask, abort, flash, redirect, render_template, request, url_for

from seed_data import SEED_STUDENTS

# --- Settings (App Service -> Settings -> Environment variables) ------------

APP_TITLE = os.environ.get("APP_TITLE", "Student Manager")
APP_COLOR = os.environ.get("APP_COLOR", "#2563eb")
APP_VERSION = os.environ.get("APP_VERSION", "v1")
COSMOS_CONNECTION_STRING = os.environ.get("COSMOS_CONNECTION_STRING", "")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "students-db")
COSMOS_CONTAINER = os.environ.get("COSMOS_CONTAINER", "students")

# App Service sets this automatically: the ID of the machine running the app.
INSTANCE_ID = os.environ.get("WEBSITE_INSTANCE_ID", "local")

# The shared database throughput, in request units per second. The Cosmos DB
# free tier covers 1000 RU/s, so 400 stays well inside it.
DATABASE_THROUGHPUT = 400

SEED_MARKER_ID = "_lab_seed_marker"
FIELDS = ("student_id", "name", "major", "year", "email")
MAJORS = (
    "Computer Science",
    "Artificial Intelligence",
    "Software Engineering",
    "Data Science",
    "Cybersecurity",
    "Business Administration",
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("student-manager")
# The Cosmos DB library logs every HTTP call; keep only its warnings.
logging.getLogger("azure").setLevel(logging.WARNING)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-for-anything-real")


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


def clean(item):
    """Keep only the student fields, dropping Cosmos DB's system fields (_rid, _etag, ...)."""
    return {"id": item["id"], **{field: item.get(field) for field in FIELDS}}


def get_student(doc_id):
    """Return the stored item for a student, or None if it doesn't exist."""
    try:
        item = get_container().read_item(item=doc_id, partition_key=doc_id)
    except CosmosResourceNotFoundError:
        return None
    return item if item.get("type") == "student" else None


def write_seed_data():
    """Write the sample students. Fixed IDs make this safe to repeat."""
    container = get_container()
    for i, student in enumerate(SEED_STUDENTS, start=1):
        container.upsert_item({"id": f"seed-{i:02d}", "type": "student", **student})


def delete_all_students():
    """Delete every student item and return how many were removed."""
    container = get_container()
    ids = [
        row["id"]
        for row in container.query_items(
            query="SELECT c.id FROM c WHERE c.type = 'student'",
            enable_cross_partition_query=True,
        )
    ]
    for doc_id in ids:
        container.delete_item(item=doc_id, partition_key=doc_id)
    return len(ids)


# --- Seed on first run --------------------------------------------------------
# The first time the app ever runs, it creates a marker item and loads the
# sample data. create_item() fails if the marker already exists, so the data
# is loaded only once: two instances starting together cannot both seed, and
# students someone deleted are never quietly brought back by a restart.

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
            log.info("SEED loaded %d sample students into container '%s'", len(SEED_STUDENTS), COSMOS_CONTAINER)
        _seed_checked = True


@app.before_request
def ensure_seeded():
    if request.endpoint != "static":
        seed_on_first_run()


# --- Helpers ------------------------------------------------------------------

def read_form():
    """Return (data, errors) from the submitted student form."""
    data = {field: request.form.get(field, "").strip() for field in FIELDS}
    errors = {}
    if not data["student_id"]:
        errors["student_id"] = "Enter a student ID, for example S1013."
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


@app.context_processor
def inject_platform_info():
    return {
        "app_title": APP_TITLE,
        "app_color": APP_COLOR,
        "app_text_color": text_color_for(APP_COLOR),
        "app_version": APP_VERSION,
        "instance_id": INSTANCE_ID,
        "majors": MAJORS,
    }


# --- Routes -------------------------------------------------------------------

@app.get("/")
def index():
    """READ: list every student, sorted by name."""
    items = get_container().query_items(
        query="SELECT * FROM c WHERE c.type = 'student' ORDER BY c.name",
        enable_cross_partition_query=True,
    )
    students = [clean(item) for item in items]
    log.info("READ %d students", len(students))
    return render_template("index.html", students=students)


@app.route("/students/new", methods=["GET", "POST"])
def add_student():
    """CREATE: add a new student item with a generated ID."""
    if request.method == "POST":
        data, errors = read_form()
        if errors:
            return render_template("form.html", mode="add", student=data, errors=errors), 400
        doc_id = uuid.uuid4().hex[:20]
        get_container().create_item({"id": doc_id, "type": "student", **data})
        log.info("CREATE student %s (%s) as item %s", data["student_id"], data["name"], doc_id)
        flash(f"Added {data['name']}.")
        return redirect(url_for("index"))
    return render_template("form.html", mode="add", student={}, errors={})


@app.route("/students/<doc_id>/edit", methods=["GET", "POST"])
def edit_student(doc_id):
    """UPDATE: change the fields of an existing student item."""
    item = get_student(doc_id)
    if item is None:
        abort(404)
    if request.method == "POST":
        data, errors = read_form()
        if errors:
            return render_template("form.html", mode="edit", student={"id": doc_id, **data}, errors=errors), 400
        get_container().replace_item(item=doc_id, body={"id": doc_id, "type": "student", **data})
        log.info("UPDATE student %s (%s), item %s", data["student_id"], data["name"], doc_id)
        flash(f"Saved changes to {data['name']}.")
        return redirect(url_for("index"))
    return render_template("form.html", mode="edit", student=clean(item), errors={})


@app.post("/students/<doc_id>/delete")
def delete_student(doc_id):
    """DELETE: remove one student item."""
    item = get_student(doc_id)
    if item is None:
        abort(404)
    get_container().delete_item(item=doc_id, partition_key=doc_id)
    log.info("DELETE student %s, item %s", item.get("name", doc_id), doc_id)
    flash(f"Deleted {item.get('name', doc_id)}.")
    return redirect(url_for("index"))


@app.post("/reset")
def reset_data():
    """Delete every student and load the sample data again."""
    removed = delete_all_students()
    write_seed_data()
    log.info("RESET removed %d students, loaded %d sample students", removed, len(SEED_STUDENTS))
    flash(f"Reset the demo data: {len(SEED_STUDENTS)} sample students are back.")
    return redirect(url_for("index"))


@app.errorhandler(404)
def not_found(_error):
    return render_template("not_found.html"), 404


if __name__ == "__main__":
    # Local run only. On App Service, Gunicorn starts the app.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), debug=True)
