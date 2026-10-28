# Student Manager: Cloud Lab Part II (PaaS on Azure)

A small student information system (Flask + Azure Cosmos DB) that students
deploy to Azure App Service: students, courses, enrollments with grades and
GPA, search and filters, a dashboard, and a Platform page that shows what App
Service is doing. Students do not edit the code; they configure it through the web
app's **Environment variables** (app settings) in the portal.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | All routes, Cosmos DB access, GPA calculation, seed-on-first-run logic |
| `seed_data.py` | 7 sample courses and 12 sample students with their enrollments and grades |
| `templates/` | `base.html` (layout, navigation, color band, version/instance panel), `dashboard.html`, `students.html`, `student_detail.html`, `student_form.html`, `courses.html`, `course_detail.html`, `course_form.html`, `platform.html`, `not_found.html` |

## Pages

| Page | What it shows |
| --- | --- |
| `/` | The dashboard when `FEATURE_DASHBOARD` is on, otherwise a redirect to `/students` |
| `/students` | All students, with search (name, ID, email) and filters (major, year); course count and GPA per student |
| `/students/<id>` | Profile: details, GPA, credits, courses and grades; enroll in a course, change a grade, drop a course |
| `/courses` | All courses, with the number of students and the average grade points |
| `/courses/<id>` | One course and its roster |
| `/platform` | Version, instance, process uptime and request count, region, pricing tier, Python version, a live database check, and the app settings (secrets hidden) |
| `requirements.txt` | Pinned dependencies, installed by App Service during deployment |

App Service starts the app on its own: it finds `app.py` with a Flask object
named `app` and runs it with Gunicorn. No startup command is needed.

## App settings

| Setting | Example | Used for |
| --- | --- | --- |
| `COSMOS_CONNECTION_STRING` | copied from the Cosmos DB account's **Keys** page | Connecting to the database (secret: never put it in the code) |
| `COSMOS_DATABASE` | `students-db` | Database name; created by the app if missing |
| `COSMOS_CONTAINER` | `students` | Container name; created by the app if missing |
| `APP_TITLE` | `Student Manager - Chadli` | Page title |
| `APP_COLOR` | `#2563eb` | Header color; changed to green for v2 in Task 6 |
| `APP_VERSION` | `v1` | Version label in the header; `v2` in the staging slot |
| `FEATURE_DASHBOARD` | not set in production; `true` in the staging slot | Turns on the dashboard. This is the new feature v2 releases in Task 6 |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` | Tells App Service to install `requirements.txt` when the zip is deployed |

The header also shows `WEBSITE_INSTANCE_ID`, which App Service sets itself.

Settings are **not** marked as deployment slot settings, so `APP_COLOR`,
`APP_VERSION` and `FEATURE_DASHBOARD` travel with the code when slots are
swapped. That is what makes the swap visible: after the swap, production has
the green header and the dashboard.

## Database

On first use the app creates the database with **400 RU/s shared throughput**
(inside the Cosmos DB free tier's 1000 RU/s) and a `students` container
partitioned on `/id`. Students, courses and the seed marker share the
container, told apart by a `type` field (`student`, `course`, `meta`). Each
student item holds its own enrollments (`[{"course_id": ..., "grade": ...}]`):
data that is read together is stored together. Deleting a course removes it
from every student's enrollments.

GPA is credit-weighted on a 4.0 scale (A = 4.0 ... F = 0); courses marked
"In progress" are not counted.

## How seeding works

On the first request, the app tries to create the item `_lab_seed_marker_v2`.
Only the first instance to do so succeeds, and it writes the sample courses
(`course-01` to `course-07`) and students (`seed-01` to `seed-12`) with fixed
IDs. The version number in the marker means a database seeded by an older
version of the app gets the new sample data once, automatically. Later instances see the marker and skip
seeding, so records a student deletes never come back on a restart.
**Reset demo data** deletes all students and courses and writes the samples again.

## Deploying (what the handout does)

```
zip -r app.zip . -x ".git/*" "README.md" "app.zip"
az webapp deploy --resource-group m351-paas-rg --name <app-name> --src-path app.zip --type zip
```

Add `--slot staging` to deploy into the staging slot in Task 6.

## Cost guardrails

- App Service plan: **Free F1** except during Task 6, when students scale up to
  **Standard S1** for deployment slots and must scale back down at the end.
- Cosmos DB: create the account with **Apply Free Tier Discount** and
  **Limit total account throughput** checked.
