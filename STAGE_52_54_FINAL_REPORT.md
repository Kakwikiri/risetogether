# RiseTogether Stages 52–54 Final Integration Report

Date: 14 July 2026  
Pre-change checkpoint: `stage-52-54-checkpoint-20260714` (`89cdf73`)

## Completed

- Added real, opt-in inactivity tracking. New users are never assumed inactive; a person is suggested only after RiseTogether has recorded at least three days away.
- Added trusted check-in suggestions for accepted friends, shared Family members, and people with a recent private conversation. Blocked users are excluded in both directions.
- Added editable “we miss you” check-ins, 7-day sender cooldown, a maximum of three check-ins per recipient per 7 days, 30-day suggestion dismissal, thanks, private reply links, and a private check-in history.
- Added a compact welcome-back card based only on stored check-ins, verified Family challenge completions, and encouragement responses created during the recorded absence.
- Added independent settings for check-in suggestions, receiving check-ins, and welcome-back summaries. Exact last-seen time is not shown.
- Consolidated all existing notification events into the existing seven preference categories. No second notification table or delivery system was created.
- Restricted phone push to important events. Likes, ordinary reactions, and routine Family chat remain available in-app but do not produce phone push or app-icon counts.
- Preserved message-preview privacy. With previews disabled, lock-screen text is only “You have a new message.”
- Secured device subscriptions with HTTPS endpoint validation, size limits, per-device unsubscribe, expired-subscription deactivation, multiple-device support, and account-delete cascade.
- Push clicks now pass through the existing notification opener, mark the notification read, and then open its validated same-origin destination.
- Added one combined app badge count from unread private messages plus unread important non-message notifications. It updates on load, focus, socket events, visibility changes, read actions, and logout, and fails safely when App Badging is unsupported.
- Added real private-message `read_at` state so unread message counts no longer depend on the older delivery flag.
- Bumped static asset and service-worker cache versions so Render clients receive the changes instead of a stale cached interface.

## Database changes

This project uses the existing idempotent startup schema-compatibility migration in `app.py`; no separate Alembic revision system exists.

- `users`: `last_active_at`, `return_summary_since`, `return_summary_dismissed_at`, plus an activity index.
- `profiles`: `checkin_suggestions_enabled`, `miss_you_notifications_enabled`, `return_summaries_enabled`.
- `messages`: nullable indexed `read_at`; existing delivered messages are backfilled only when the column is first created.
- `notifications`: indexed `important`; existing important legacy categories are backfilled.
- New `return_checkins` table with sender/recipient foreign keys, message, timestamps, self-send constraint, and delete cascades.
- New `return_suggestion_dismissals` table with viewer/subject foreign keys, expiry, a unique pair constraint, and delete cascades.

All changes are additive and preserve existing rows.

## Files changed

- `app.py`
- `helpers.py`
- `models.py`
- `notifications_service.py`
- `routes/api.py`
- `routes/chat.py`
- `routes/family.py`
- `routes/main.py`
- `static/css/styles.css`
- `static/js/app.js`
- `static/js/socket.js`
- `static/service-worker.js`
- `templates/base.html`
- `templates/feed.html`
- `templates/notifications.html`
- `templates/reauthenticate.html`
- `templates/return_checkins.html` (new)
- `templates/settings.html`
- `tests/test_security_regressions.py`
- `tests/test_stage_52_54.py` (new)
- `STAGE_52_54_FINAL_REPORT.md` (new)

## Verification completed

- 91 automated tests pass.
- Python compilation and SQLAlchemy relationship configuration pass (65 tables).
- All changed Jinja templates compile.
- JavaScript syntax checks pass for the app, socket client, and service worker.
- All template `url_for` references resolve against the registered blueprints (136 routes checked; no missing endpoint).
- Every literal notification event used by current routes maps to one of the seven preference categories.
- `git diff --check` passes.

## Unfinished or intentionally paused

- Family voice rooms remain clearly labeled as unavailable. They require reliable voice infrastructure and were not replaced with fake functionality.
- The existing Family leaderboard empty state and paused Points empty state remain “Coming soon.” No working data was removed.
- Video/audio calling and Live infrastructure were outside these stages and remain excluded from final feature work.
- A full real-device background-push delivery test requires Render’s VAPID configuration and an installed PWA.

## Browser and device limitations

- App icon badging depends on the installed browser, PWA installation, Android/iOS version, and launcher. Unsupported devices continue to show accurate in-app and title counts without errors.
- Some launchers decide how counts above nine are rendered. RiseTogether’s own title and navigation render `9+`; the numeric Badging API value is supplied to supported launchers.
- Push requires HTTPS, notification permission, a working service worker, valid `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT`, plus the `pywebpush` dependency.
- Browser permission cannot be restored by the site after a user blocks notifications; it must be changed in browser/site settings.

## Security and release concerns

- Push endpoints and keys are sensitive authentication material. Keep database access restricted and never log subscription keys.
- VAPID private keys must remain only in Render environment variables and must never be committed.
- Check-in trust and cooldown rules are enforced on the server, not only hidden in the UI.
- Notification redirect destinations are restricted to same-origin paths to prevent open redirects.
- The local PostgreSQL service was unavailable during this audit, so production-data migrations and end-to-end database flows must be observed on the Render deployment before public release.
- Review Render logs immediately after the first deployment for migration errors, push delivery 404/410 responses, or missing VAPID configuration.

## Manual Render testing

1. Deploy the pushed `main` branch and confirm startup migrations complete once without errors.
2. Use two test accounts that are accepted friends or share a Family. Verify ordinary unrelated accounts cannot send or dismiss a check-in.
3. Temporarily set one test user’s `last_active_at` to more than three days ago in the test database. Confirm the suggestion is vague, editable, dismissible, and contains no exact timestamp.
4. Send a check-in. Verify the receiver gets one clickable notification, the sender cannot repeat it for seven days, and the recipient is capped at three check-ins in seven days.
5. Return as the inactive user. Confirm the welcome card contains only real activity, and test Thank, Reply privately, related Family links, and Dismiss.
6. Turn each connection/return preference off in Settings and repeat the relevant flow.
7. Install the PWA on two devices. Enable notifications from Settings on each device; verify permission was not requested on first visit.
8. Test an important private message, friend request, encouragement, check-in, Family involvement, challenge approval, and admin notice. Test a like/reaction and routine Family chat do not generate phone push.
9. Disable message previews and verify no private message content appears on the lock screen.
10. Tap every push type and confirm it marks the notification read and opens the exact conversation, post, profile, Family, or challenge.
11. Test unread counts after receive, open, mark read, mark all read, login, logout, foreground socket delivery, and background push.
12. Test mobile, tablet, and desktop in light and dark themes; inspect the console and Render server logs.

## Recommended before public release

- Complete the Render/manual matrix above on at least Chrome Android, Samsung Internet, and one desktop Chromium browser.
- Run a staged push test with previews both enabled and disabled before inviting real users.
- Configure monitoring for failed pushes and migration/startup failures without recording private notification content.
- Back up the production database before the first deployment containing these additive schema changes.
