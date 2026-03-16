# Whisper Retention Policy

**Policy Version:** 2026-03-v1

## Purpose

Whisper stores some records for legal, compliance, trust, and operational reasons.
V1 retention cleanup is intentionally narrow. It only targets low-risk transient
records that are safe to remove without weakening legal evidence or core product
history.

## Record Categories

### Auth tokens

`AuthAccessToken` stores magic-link and QR sign-in tokens. These are short-lived
authentication artifacts and are safe cleanup targets after they expire or are used.

### Access requests

`AccessRequest` stores onboarding, verification, manual review, waitlist, and
approval/rejection history. Some unfinished records are safe to clean up, but rows
that are linked to a real account or contain moderation history must be handled
carefully.

### Django sessions

Whisper uses Django sessions for authenticated app access. Expired session rows are
low-risk and can be cleaned with Django's built-in `clearsessions` command.

### Agent accounts

`AgentUser` is the primary account record. It stores legal acceptance state and
account lifecycle data. Agent accounts are not auto-deleted in V1.

### Opportunities

`Listing` is the core opportunity record. It stores opportunity details, live/stale
status, and certification fields. Opportunity records are not auto-deleted in V1.

### Legal acceptance records

Legal acceptance is stored on `AgentUser`, including accepted terms/privacy versions,
timestamps, IP address, and user agent. These records are legal evidence and are not
auto-deleted in V1.

### Opportunity certification records

Opportunity certifications are currently stored on `Listing` as live booleans. They
include seller-direction, compliance, private-sharing, and information-accuracy
certifications. These are evidence-bearing fields and are not auto-deleted in V1.

### Notifications and logs

Whisper stores collection alert email logs, in-app notifications, and reminder
history. These are operational records. They are not part of V1 auto-cleanup.

## What Is Currently Auto-Cleaned

- Expired and used `AuthAccessToken` rows
- Stale uncompleted `AccessRequest` rows with no linked `AgentUser`
- Stale rejected `AccessRequest` rows with no linked `AgentUser` and no moderation history
- Expired Django sessions via `clearsessions` when session cleanup is enabled

## Approved V1 Cleanup Categories

- `auth_tokens.qr_expired`
- `auth_tokens.qr_used`
- `auth_tokens.non_qr`
- `access_requests.pending_or_waitlist`
- `access_requests.rejected`

Any new cleanup category must be reviewed before it is activated.

## What Is Explicitly Protected From Auto-Deletion In V1

- `AgentUser` records
- Legal acceptance evidence on `AgentUser`
- `Listing` / opportunity records
- Opportunity certification fields
- Reviewed or moderated `AccessRequest` rows
- Any record that materially answers who did what and when

## Where Retention Logic Lives

- `whisper/settings.py` for retention settings
- `listings/retention.py` for cleanup selectors
- `listings/management/commands/cleanup_retention.py` for enforcement

## How To Run Retention Safely

Dry run:

```bash
python manage.py cleanup_retention --dry-run
```

Delete safe targets:

```bash
python manage.py cleanup_retention
```

Session cleanup only:

```bash
python manage.py clearsessions
```

Note: Django's `clearsessions` command does not support a dry-run preview. The
Whisper retention command reports that limitation instead of guessing a count.

## Important Cautions

- Startup cleanup is opportunistic. It runs on app start, not on a guaranteed daily schedule.
- Deep-storage or archive handling is not implemented yet.
- Legal and compliance evidence should not be deleted casually.
- `AccessRequest` protection currently relies on email matching to `AgentUser`, not a direct FK.

## Known Limitations

- Startup cleanup is opportunistic, not scheduled.
- `AccessRequest` protection currently relies on email matching rather than FK linkage.
- Legal and compliance evidence is not archive-ready yet.
