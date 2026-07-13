# RiseTogether Project Audit and Safe Improvement Plan

Audit date: 2026-07-13. Scope: the current Flask application, models, routes, templates, JavaScript, CSS, authentication, feed, Families, posts, challenges, polls, quizzes, chat, notifications, moderation, deployment configuration, and tests. Stage 1 does not redesign or remove product functionality.

## Current architecture

- Flask application assembled in `app.py` with SQLAlchemy, Flask-Login, Flask-SocketIO in threaded mode, PostgreSQL, server-rendered Jinja templates, a PWA service worker, and vanilla JavaScript.
- Blueprints: `auth`, `main`, `family`, `chat`, `moderation`, and `api`.
- Authentication supports password login, fresh-login checks for sensitive administration, password reset codes, legacy token redirects, and Google OAuth. Passwords and reset phrases are Werkzeug hashes. Bans are checked during authentication and Socket.IO connection.
- The project has no Flask-Migrate/Alembic directory. `ensure_schema_compatibility()` calls `db.create_all()` and runs PostgreSQL DDL/DML during every application startup; `setup_db.py` contains a smaller second set of schema updates. This is the highest data-management risk and must be replaced by versioned, reversible migrations before substantial schema work.

## Database models and relationships

- Identity/social: `User` has one `Profile`; one-to-many posts, Family memberships, sent/received messages, notifications, reports, reset tokens, help requests, push subscriptions, and audit relationships. `Follow`, `Block`, and `FriendRequest` connect two users. Pair uniqueness exists for follows and friend requests; Block currently has no pair uniqueness constraint.
- Posts: `Post` belongs to an author and optionally a Family, with dependent `Reaction`, `Comment`, `Report`, and `PostShare` records. Comments support parent/reply relationships. Reaction uniqueness is `(post_id, user_id, type)` and comment-like uniqueness is `(comment_id, user_id)`. A user can therefore select several distinct reaction types on one post by design.
- Families: `Family` belongs to an optional owner and has memberships, posts, messages, polls, challenges, quizzes, restrictions, and moderation logs. `FamilyMember` is unique by `(family_id, user_id)`.
- Polls: `FamilyPoll` has ordered `FamilyPollOption` rows and `FamilyPollVote` rows. A vote is unique per `(poll, option, user)`; route logic enforces single-choice polls and vote-change policy.
- Challenges: `FamilyChallenge` stores server-controlled points and has `ChallengeCompletion` rows unique by `(challenge_id, user_id)`. Completion evidence may reference an uploaded asset. Route logic verifies Family membership, challenge ownership/family, active dates/status, and duplicate completion before insertion.
- Quizzes: `Quiz` has questions, choices, attempts, and answers. Question points and awarded points are computed server-side. Attempts belong to the current user; route logic controls repeat attempts and validates selected choices against their question.
- Chat/communications: `Message` supports private or Family messages, media, replies, view-once expiry, pinning, and per-user `MessageDeletion`. `Notification` is a generic category/message/action record; `PushSubscription` stores web-push credentials. `LiveSession` represents broadcasts.
- Operations: `Report`, `HelpRequest`, `SiteSetting`, `PasswordResetToken`, `AuditLog`, `MediaAsset`, `FamilyModerationLog`, and `FamilyMemberRestriction` support moderation and operations.

## Points and completion behavior

- There is no persistent user wallet, balance, reward claim, or points ledger.
- Point fields are `FamilyChallenge.points`, `QuizQuestion.points`, `QuizAttempt.score`, `QuizAnswer.awarded_points`, plus calculated dashboard totals (`challenge_points`, `quiz_points`, `total_points`).
- Challenge points are derived from the challenge record and unique completion rows; browser-sent point totals are not trusted. Quiz score is calculated from database choices and question points.
- Existing database uniqueness blocks duplicate challenge completions. Route pre-checks provide friendly feedback; concurrent inserts can still raise `IntegrityError`, so a later hardening stage should translate that race into an idempotent response. Quiz repeat protection is route-only and has no database constraint for single-attempt quizzes.

## Family roles and permissions

- Roles: `owner`, `admin`, `moderator`, and `member`, ranked in that order.
- Owner only: edit Family, manage roles, delete Family.
- Owner/admin: change image, manage members, suspend members, create polls/challenges/quizzes, invite.
- Owner/admin/moderator: warn and mute members.
- Server-side helpers normalize roles and check every management action. Owners are synchronized to an owner membership at startup. Website moderation separately uses `super_admin`, `admin`, `moderator`, and member roles with rank checks, fresh-login protection for sensitive actions, last-super-admin protection, and audit logging.

## Media system

- Uploads are stored under `UPLOAD_FOLDER` and mirrored into `MediaAsset` database blobs for resilience. `/api/uploads/<filename>` serves a disk file first and falls back to the database asset.
- Names use `secure_filename` and collision suffixes. Global and per-type size limits are configured, images are resized/optimized, and videos may be transcoded to browser-friendly H.264/AAC. Family/profile images are additionally verified by Pillow.
- Stage 1 now validates file signatures for all accepted image, document, audio, and video extensions before saving. This prevents simple executable/content spoofing by renaming a file. Continue to serve uploads with safe content disposition and add malware scanning before allowing higher-risk document sharing at scale.

## Socket.IO and chat

- Client events: `connect`, `disconnect`, `join_room`, `private_message`, `mark_messages_delivered`, `family_message`, `webrtc_offer`, `webrtc_answer`, `ice_candidate`, `call_invite`, `call_accepted`, `ready_for_call`, `call_ended`, `call_declined`, `leave_call`, `join_live`, `live_offer`, `live_answer`, `live_ice_candidate`, and `live_comment`.
- Server emissions include presence/status, private and Family messages, delivery/deletion events, notification delivery, call lifecycle/signaling, live viewer/host lifecycle, live signaling, comments, and viewer counts.
- Message creation verifies the current user, block/ban state, recipient existence, Family membership, and Family mute/suspension state. HTTP message actions verify message access.
- Fixed in Stage 1: generic room subscription now permits only a canonical private room containing the current user or a Family room where the current user is a member. Previously any authenticated client could request an arbitrary room name.
- Remaining hardening: signaling endpoints should consistently verify that sender and target belong to the active call/live session, add payload/rate limits, and move process-local presence/call state to a shared backend before multi-worker deployment.

## Feed, reactions, and notifications

- Feed filters: public, own, Family membership, friends, and directly shared posts; blocked users and hidden posts are removed by `can_view_post`. Query search matches content, author username, and display name. `?type=videos` selects videos. Trending uses reactions ×2 + comments + shares ×3.
- Post audiences are `public`, `friends`, `family`, and `private`; post creation verifies Family membership and restrictions. Post view, reaction, comment, and share paths re-check visibility server-side.
- Notification categories observed: `followed_post`, `follow`, `reaction`, `share`, `comment`, `mention`, `friend_request`, `live`, `message`, `voice_note`, `video_note`, `family_chat`, `call`, `family_image_changed`, `poll_created`, `family_poll`, `poll_closed`, `challenge_created`, `quiz_starting`, `family_invite`, `family_role`, and `family_moderation`.
- Notifications are persisted, emitted to a per-user Socket.IO room, optionally sent through Web Push, individually/collectively marked read, and ownership-checked when opened. Category values are free-form strings rather than an enum/check constraint.

## CSS and reusable UI

- `static/css/styles.css` is a mobile-first stylesheet with shared variables for ink, muted text, panels, soft backgrounds, borders, brand colors, accent, danger, and shadows. Dark theme overrides reuse the same variables.
- Reusable patterns include `.card`, `.panel`, `.button` and variants, form grids/stacked forms, alerts, status/file pills, modals, cards for posts/Families/people/notifications, profile sections, chat message/layout primitives, Family dashboard panels, poll/challenge/quiz cards, meters, badges, and admin filter/action layouts.
- Responsive breakpoints exist at 900, 768, 560, and 380 pixels. New UI should extend these variables and primitives rather than introduce a second design system.

## Fragile or duplicated areas

- Startup schema mutation is duplicated between `app.py` and `setup_db.py`; `app.py` also contains a duplicated `UPDATE users SET admin_role...` statement. DDL during web startup can race across workers, prolong boot, fail after partial changes, and makes rollback/audit difficult.
- Notification construction/emission is repeated in `main.py`, `family.py`, and `chat.py`; this risks inconsistent push behavior and category spelling.
- Upload validation and image validation are split across helpers and route modules. Family/profile implementations are nearly identical.
- Family restriction lookup and role/permission-related logic is duplicated across main/chat/family modules.
- `app.py`, `routes/family.py`, `routes/chat.py`, `static/js/socket.js`, and the single CSS file are large and tightly coupled, increasing regression risk.
- Several dashboards load full collections and calculate counts in Python, which will become slow as data grows. Feed and notifications are unpaginated.
- `PostShare` lacks a uniqueness constraint for `(post_id, user_id, recipient_id)` even though route logic deduplicates it. `Block` lacks blocker/blocked uniqueness. Add these only through migrations after measuring/cleaning existing duplicates.

## Security and safety status

- Added global session-token CSRF validation for every unsafe HTTP method. All existing forms receive a hidden token and same-origin JavaScript requests receive the token header. Invalid requests are logged and receive a friendly 400 response.
- Server-side authorization is broadly present: login requirements, content ownership, post visibility, Family membership/roles, block/ban checks, admin rank checks, fresh-login checks, and last-super-admin safeguards. The Socket.IO room authorization gap was fixed.
- Uploads now combine allowlisted extensions, signature verification, size limits, secure names, and image decoding checks. Upload filenames remain publicly addressable; sensitive evidence should receive explicit access policy in a later stage.
- Unexpected 500 errors now receive a user-friendly response and are logged with request path/method and exception information. Existing admin actions and Socket.IO diagnostics already write structured log messages.
- Feature flag convention is available as `feature_enabled("name")`, backed by `FEATURE_<NAME>_ENABLED`. New features default off unless their call site explicitly chooses a true default. `REALTIME_MEDIA_ENABLED` remains the existing dedicated media flag.
- Secrets have development fallbacks. Production must set a strong `SECRET_KEY`, database credentials, OAuth/SMTP secrets, VAPID secrets, and TURN credentials. Startup should refuse the default secret in production in a later deployment-hardening change.
- `ProxyFix` trusts one forwarded hop; deploy only behind the expected trusted proxy. External action URLs are stored but current notification redirects use locally generated URLs.
- No rate limiting currently protects login, password reset, reports, messages, comments, live comments, uploads, or OAuth initiation. Add shared-store rate limiting before public scale.
- Logout is a GET route and should become CSRF-protected POST in a compatibility-reviewed stage.

## Safe implementation sequence for later stages

1. Establish a baseline: back up PostgreSQL and uploads, record row counts/constraints, add end-to-end smoke coverage for authentication, feed, Family roles, challenge idempotency, polls, quizzes, chat, notifications, upload rejection, and admin ranks.
2. Introduce Flask-Migrate/Alembic by generating a baseline matching production. Reconcile `ensure_schema_compatibility()` and `setup_db.py`, then stop running DDL at web startup only after verified deploy/rollback rehearsals.
3. Add measured safe constraints in migrations: Block pair uniqueness, PostShare recipient uniqueness, role/status/audience checks, nonnegative point checks, and useful foreign-key indexes. Clean duplicates transactionally before constraints; never delete user-owned content.
4. Centralize authorization policies, notification creation, upload policy, and Family restrictions. Preserve route URLs and response behavior while adding policy-focused tests.
5. Introduce an append-only points/reward ledger only when a requested stage defines reward rules. Use immutable event keys and database uniqueness for exactly-once awards; never accept reward values from browsers.
6. Add rate limiting, access-controlled sensitive media, shared Socket.IO presence/call state, pagination, database-side aggregates, and operational health/error monitoring behind feature flags.

## Stage 1 validation checklist

1. Set a test database and a non-default `SECRET_KEY`; back up any database used for testing.
2. Run `python -m unittest discover -s tests -v`.
3. Start with `python app.py`, then verify signup, login, logout, password reset pages, feed search/video filter, profile editing, post create/react/comment/share/hide/delete, people/follow/friend actions, and notification read actions.
4. Create/join a Family as two users. Verify owner/admin/moderator/member boundaries, image update, invitation, poll creation/voting/closing, one challenge completion only, quiz scoring/repeat policy, Family chat, and restrictions.
5. Verify direct text/media chat, delivery state, reply, forward, pin, delete, view-once, voice/video call signaling, live session/comment/end, and Web Push if VAPID is configured.
6. Verify admin access at each website role and fresh-login requirements for destructive/sensitive actions.
7. Submit a POST without `csrf_token` (or the `X-CSRF-Token` header) and confirm HTTP 400; refresh and submit normally to confirm success.
8. Rename a text/executable file to an allowed media/document extension and confirm it is rejected; upload genuine JPG/PNG/GIF/PDF/DOCX/audio/video samples within limits and confirm they still work.
9. With two accounts, attempt to emit `join_room` for another private chat or an unjoined Family and confirm `room_join_denied`; confirm owned private and joined-Family rooms still receive messages.

No database migration is added in Stage 1 because the repository has no migration framework and these safety fixes require no schema change.
