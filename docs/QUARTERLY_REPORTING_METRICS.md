# Quarterly reporting metrics and scorecards

Quarter windows use Africa/Lagos time. Q3 runs from 1 July midnight inclusive to 1 October midnight exclusive. Inclusive `to` values from the existing quarterly API are normalized to include the final fractional second.

## Snapshot format

Reporting semantics **version 3** stores nullable `MemberQuarterlyReport.activity_details` and `CommunityQuarterlyStats.ecosystem_stats`. Null on an older snapshot means the detail was not collected. Each extended source has `available`, `metrics`, `details`, and `notes`. A failed source is unavailable with no fabricated zero metrics; a successful source may legitimately report zero.

Regeneration updates existing member rows and preserves their leaderboard opt-out choices. Individual computation failures leave the quarter incomplete, retain existing rows, and prevent distribution. Incompatible historical quarters do not show comparison percentages.

## Domain-owned sources

All seven GET endpoints require a service JWT and accept `from` and `to`. Reporting fetches them over HTTP; each service queries only its own domain tables. Members and wallet also accept `member_auth_id` for private individual detail.

| Endpoint | Definitions |
|---|---|
| `/internal/members/reporting-summary` | Clubs created before quarter-end; pods not dissolved at quarter-end; distinct pods/members assigned during the quarter and populated at its end; overlapping dated Club enrollments; published plan versions covering the quarter; confirmed Experience orders, named tickets, separate package entitlements, actual check-ins and distinct attendees. |
| `/internal/sessions/reporting-summary` | Calendar sessions separately from completed-status sessions; sessions with recorded swimmer check-ins; member/guest visits from Attendance; group booking state, attached guest reservations, guest passes and current conversion linkage. Reservations are not actual attendance. |
| `/internal/wallet/reporting-summary` | Completed transactions only: rewards, purchases, top-ups, refunds and admin adjustments; distinct recipients/users; transaction direction; checkout reference and service source. Credits/debits remain available for compatibility, with explicit labels. |
| `/internal/media/reporting-summary` | Vault creation and capture dates; vaults populated by original uploads in the window; original uploads, originals in vaults, contributors and originals published in the window. Excludes derivatives, known duplicates and currently deleted assets. No media/access URLs. |
| `/internal/communications/reporting-summary` | Articles by publication date; podcasts by original broadcast date when set, otherwise content publication date. Reports title, episode number, date basis and public video link. External broadcasts require registration to enter the canonical count. |
| `/internal/ai/reporting-summary` | Stroke Lab submissions by creation date and current status/source, plus completion events dated within the quarter. Public submissions are separated from member activity. |
| `/internal/academy/reporting-summary` | Overlapping cohorts, starts and scheduled ends, recorded certificates/progress updates, enrollments and location totals. An ended cohort does not establish learner graduation. |

Enrollment states, media deletion state and guest conversion links are current state at computation. Their source tables do not establish a complete history of state transitions. Methodology notes travel with the snapshot.

## Location data

Known aliases normalize Yaba/Rowe Park, VI/Oduduwa House, Festac/Siloam and Ikoyi/Herel Play. Each location shows calendar/completed sessions, check-ins, distinct member swimmers, duration-based member swimmer-hours, configured Clubs/pods and populated pods at quarter-end. Pool IDs are preserved where present. Configuring a Club does not prove an operational pool. Location member counts overlap; do not sum them into a community total.

Internal attendance identifiers are used to deduplicate swimmers, then removed before community persistence. Pod rosters and session attendee identifiers never appear in the public community snapshot.

## Individual scorecards

- Programme attendance and streaks retain the member's quarter programme commitments. Academy remains primary where Academy and Club commitments overlap.
- All recorded swims, attended session types and locations come from actual swimmer/guest attendance matched to session dates. Retrospective entry dates do not move a Q3 swim into Q4. Coach/volunteer attendance is excluded.
- Pool hours estimate the full scheduled duration of recorded attended swims, plus exact converted GuestPass minutes. One-hour lessons count as one hour; zero-duration sessions need source correction. These are not measured time in the water or distance.
- `sessions_by_type` remains the legacy expected-session breakdown. `activity_details.attendance.attended_by_type` is the actual attended breakdown; the UI names both explicitly.
- Reward Bubbles come from completed reward transactions, independently of top-ups/refunds. The compatibility fields `bubbles_earned` and `bubbles_spent` mean gross completed credits/debits; legacy cards label them wallet credits/debits. The rewards leaderboard excludes legacy rows without a reward measure.
- Percentiles compare active swimmers within the same programme using strictly fewer programme attendances; inactive member profiles do not inflate rankings. Unsupported traffic/distance metaphors are removed from generated cards.
- Private member detail includes Club/pod participation, Experience check-ins, purchase Bubbles and source availability. CSV exports carry these definitions too.

## Quarter editorial and podcasts

`services/reporting_service/assets/quarterly_highlights.json` supplies curated feature highlights and explicitly attributed external episode records. Features and planning are separate from measured uptake. The Q3 file includes Club plans, guest/group booking, private Media Vaults, expanded checkout, Experience ticketing, volunteer automation, Stroke Lab refinements and curriculum planning.

The team reports two Q3 Beyond the Pool episodes. Episode 2's 3 September broadcast date is verified on YouTube. Episode 1's original date remains unconfirmed because its current upload is in October. The reported 1 October Episode 3 belongs to Q4. These external records are displayed separately from canonical podcast totals to prevent double counting.

`ContentPost.episode_air_date` stores the original broadcast date independently of `published_at`; the admin content form supports it. Confirm the original date before entering Episode 1 into a dated canonical report. Content creation can invoke existing notification workflows, so registering public episodes is a separate operational action.

## Deployment and regeneration

1. Apply the reporting migration `d4e8f1a6b209` and communications migration `e2f6a9c1d308` through the normal release process. Both are nullable additions; legacy snapshots are preserved.
2. Deploy the Academy, members, sessions, attendance, wallet, media, communications, AI and reporting service changes together with compatible frontend types. Extended services may be deployed before reporting; an unavailable endpoint produces an explicit unknown source.
3. Review attendance coverage/session status, missing Academy progress and possible duplicate identities. Resolve records using the appropriate service's normal workflows.
4. Regenerate the quarter through the authorized admin generation route. Check snapshot version 3, completed status, source availability, locations, individual data and the business review. Regenerate Q2 under compatible definitions before publishing growth comparisons.
5. Review the concrete report before any separate publication or email operation. Email dispatch checks the completed corrected snapshot, individual attendance availability and duplicate identity gate.

This change supplies local code, tests and report artifacts. It does not run migrations, regenerate production reports, register public episodes, publish social posts or send member emails.

## Validation

Targeted tests cover quarter boundaries, completed wallet categories, missing-source semantics, location deduplication/privacy, mixed programme attendance, one-hour and retrospective swims, coach exclusion, opt-out preservation, partial regeneration failure and authentication. Optional local PostgreSQL tests verify nullable migration upgrades/downgrades, original podcast dates, historical pod assignment windows and the rewards leaderboard's JSONB query. Frontend tests verify configured/populated groups, confirmed zero versus unknown, editorial highlights and unconfirmed episode dates.
