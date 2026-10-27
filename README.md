# Student Manager: Cloud Lab Part II (PaaS on Azure)

A small Flask + Azure Cosmos DB CRUD app that students deploy to Azure App
Service. Students do not edit the code; they configure it through the web
app's **Environment variables** (app settings) in the portal.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Routes for list, add, edit, delete and reset; Cosmos DB access; seed-on-first-run logic |
| `seed_data.py` | The 12 sample students |
| `templates/` | `base.html` (layout, color band, version/instance panel), `index.html`, `form.html`, `not_found.html` |
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
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` | Tells App Service to install `requirements.txt` when the zip is deployed |

The header also shows `WEBSITE_INSTANCE_ID`, which App Service sets itself.

Settings are **not** marked as deployment slot settings, so `APP_COLOR` and
`APP_VERSION` travel with the code when slots are swapped. That is what makes
the swap visible.

## Database

On first use the app creates the database with **400 RU/s shared throughput**
(inside the Cosmos DB free tier's 1000 RU/s) and a `students` container
partitioned on `/id`. Students and the seed marker live in the same container;
student items have `"type": "student"`, the marker has `"type": "meta"`.

## How seeding works

On the first request, the app tries to create the item `_lab_seed_marker`.
Only the first instance to do so succeeds, and it writes the 12 sample students
with fixed IDs (`seed-01` to `seed-12`). Later instances see the marker and skip
seeding, so records a student deletes never come back on a restart.
**Reset demo data** deletes all student items and writes the samples again.

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
