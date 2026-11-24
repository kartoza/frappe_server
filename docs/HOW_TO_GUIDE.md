# How To Use the Frappe Platform

A complete, start-to-finish walkthrough of the platform: logging in, standing up a
bench, creating a site on it, installing apps, backups, SQL access, and inviting
teammates. Every screenshot below was taken against a real running instance.
Nothing is mocked.

## Contents

1. [Logging in](#1-logging-in)
2. [The Sites dashboard](#2-the-sites-dashboard)
3. [Benches](#3-benches)
4. [Creating a bench](#4-creating-a-bench)
5. [The bench detail page](#5-the-bench-detail-page)
6. [Fetching or updating an app on a bench](#6-fetching-or-updating-an-app-on-a-bench)
7. [Creating a site](#7-creating-a-site)
8. [Watching a job run](#8-watching-a-job-run)
9. [The site overview tab](#9-the-site-overview-tab)
10. [Installing apps on a site](#10-installing-apps-on-a-site)
11. [Backups](#11-backups)
12. [Restoring from an upload](#12-restoring-from-an-upload)
13. [Job history](#13-job-history)
14. [The app catalog](#14-the-app-catalog)
15. [Managing users](#15-managing-users)
16. [Accepting an invite](#16-accepting-an-invite)
17. [Your profile](#17-your-profile)
18. [The SQL playground](#18-the-sql-playground)
19. [Deleting a site](#19-deleting-a-site)

---

## 1. Logging in

Open the platform in a browser and sign in with your email and password. If you
don't have an account yet, an admin needs to invite you first (see
[Accepting an invite](#16-accepting-an-invite)).

![Login page](images/01-login.png)

## 2. The Sites dashboard

This is the landing page after login, listing every site across every bench,
with the apps installed on each and its current status. Click any site to
open it, or **New site** to create one.

![Sites dashboard](images/02-dashboard-sites.png)

## 3. Benches

A **bench** is a Frappe/ERPNext installation pinned to one Frappe version
(branch). Each bench can host multiple sites, and each site on it shares that
bench's installed apps. The Benches page lists every bench and lets you create
new ones.

![Benches list](images/03-benches-list.png)

## 4. Creating a bench

Give it a name and pick a Frappe branch (`version-14`, `version-15`,
`version-16`, or `develop`). There's no restriction tying a bench's apps to
this branch: you choose each app's branch separately when you fetch it (see
[step 6](#6-fetching-or-updating-an-app-on-a-bench)).

![Create bench form](images/04-create-bench-form.png)

Submitting takes you straight to the bench's detail page, which shows live
output while it provisions. This takes a few minutes the first time, since
it's building a fresh Frappe installation from scratch.

## 5. The bench detail page

Once a bench is active, its detail page is the hub for everything on it: the
sites running on it, and every app that's been fetched onto it (with the branch
each was fetched at).

![Bench detail page](images/05-bench-detail.png)

Notice some apps are "registered as" a different name than the catalog entry:
that's the real name Frappe gave the app internally (derived from its own
code, not necessarily matching what it's called in the catalog). The platform
tracks this automatically so install/update/remove always use the right name.

## 6. Fetching or updating an app on a bench

Click **Fetch to bench** (or **Update**, if it's already fetched) next to any
app to expand a branch picker. Pick a branch and confirm; this pulls that
app's code into the bench. If you're updating an already-installed app, every
site on the bench that has it installed gets migrated afterwards automatically.

![Branch picker expanded](images/06-branch-picker.png)

This never restarts the bench container, so every other site on it stays up
throughout.

## 7. Creating a site

From a bench's page, click **+ New site**. Pick a hostname (must fall under an
allowed domain; see the hint text under the field, configured by your admin
via `ALLOWED_SITE_DOMAINS`), an admin password, and which already-fetched apps
to install at creation time.

![New site wizard](images/07-new-site-wizard.png)

You can also create a site with no apps and install them later (see
[step 10](#10-installing-apps-on-a-site)).

## 8. Watching a job run

Every action that runs a real command against a bench or site (creating,
installing an app, backing up, restoring) is tracked as a **Job** with live
streaming output, so you can see exactly what's happening (and it survives a
page refresh).

![Site creation job in progress](images/08-site-job-progress.png)

## 9. The site overview tab

Once a site is active, its detail page opens on the Overview tab: trigger a
backup, run pending migrations, or toggle maintenance mode. Maintenance mode is
the platform's equivalent of "stop": a bench container hosts multiple sites,
so there's no single container to stop for just one of them.

![Site overview tab](images/09-site-overview.png)

## 10. Installing apps on a site

The Apps tab splits into **Installed** and **Available**: available meaning
"fetched onto this site's bench, but not installed here yet." Click **Install**
to add one.

![Apps tab before installing](images/10-site-apps-before-install.png)

Once it finishes, it moves to Installed, with an **Uninstall** action next to
it (which removes that app's data from the site's database).

![Apps tab after installing](images/11-site-apps-after-install.png)

## 11. Backups

Every backup you trigger, whether from the Overview tab or automatically
before some restore operations, shows up here with its size and a direct
download link.

![Backups tab](images/12-site-backups.png)

## 12. Restoring from an upload

Independent of the tracked backups above, you can restore a site directly from
files you provide: a SQL dump, a `site_config.json`, or a zip/tar of public or
private files (including straight from a real `bench backup --with-files`
output; the platform detects and strips that nesting automatically).

![Restore tab](images/13-site-restore.png)

## 13. Job history

Every job ever run against this site (creation, installs, backups, restores,
migrations) is listed here with its outcome and timestamp.

![Jobs tab](images/14-site-jobs.png)

## 14. The app catalog

Under **App catalog**, register the apps available to fetch onto any bench:
just a name and a git repository URL. Which branch to pull is always chosen
per-bench when you actually fetch it (step 6), not restricted here.

![App catalog page](images/15-app-catalog.png)

## 15. Managing users

Admin-only. Invite a new user by email (optionally granting them admin rights
too); this generates a one-time invite link. An email is also attempted, but
since that requires SMTP to be configured, the link itself is always shown so
you can share it directly.

![Users page](images/16-users-page.png)

After sending an invite, the link appears right there for you to copy, and the
invite shows up as pending below until it's accepted or revoked.

![Invite link shown after sending](images/17-invite-link.png)

From this page you can also promote/demote admins, deactivate, or remove any
user (you can't do any of these to your own account; there always has to be
at least one admin able to manage the others).

## 16. Accepting an invite

Whoever receives the invite link lands on a public page (no login required) to
set their password and create their account.

![Accept invite page](images/18-accept-invite.png)

Submitting logs them straight in.

## 17. Your profile

Every user, not just admins, can update their display name and change their
own password from the Profile page.

![Profile page](images/19-profile.png)

## 18. The SQL playground

Admin-only. Pick any site's database (labelled with the site hostname it
belongs to) and run SQL directly against it: full read and write access,
including DDL, with no restrictions on the statements themselves. Separate
multiple statements with `;` to run them as a batch.

![SQL playground with a query result](images/20-sql-playground.png)

This is a genuine power tool: statements run immediately with no undo. It's
provided for the same reason the platform already mounts the Docker socket:
an admin here already has full reach over everything this platform manages.

## 19. Deleting a site

From a site's page, **Delete site** (top right, next to the status badge; see
the screenshot in [step 9](#9-the-site-overview-tab)) drops its database and
files and removes it from routing. You'll be asked to confirm, since this
can't be undone.

---

That covers the full lifecycle: bench, app, site, day-to-day operation, and
teardown. For infrastructure-level configuration that doesn't have a UI of its
own, such as SSL/Let's Encrypt automation, the site hostname whitelist, or
email delivery for invites, see the comments in `.env.example`.
