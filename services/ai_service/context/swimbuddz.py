"""Canonical editorial context for SwimBuddz article generation.

This is deliberately curated rather than assembled from live databases or the
codebase. It gives the model all stable product context relevant to an article
without leaking member data or encouraging it to invent changing facts.
"""

SWIMBUDDZ_CONTENT_CONTEXT_VERSION = "2026-07-13.v1"

SWIMBUDDZ_CONTENT_CONTEXT = """
SwimBuddz is an adult swimming ecosystem based in Lagos, Nigeria, with an
Africa-first mission: help adults become safer, more confident, more capable
swimmers and sustain swimming through community, practice, and coaching.

AUDIENCE AND REALITY
- The primary audience is adult beginners and improvers. Some members may be
  learning from zero, rebuilding confidence after a frightening experience,
  returning after years away, or refining technique and endurance.
- Advice must respect common adult concerns: fear, breath control, body image,
  consistency, busy schedules, pool access, safety, and gradual progress.
- Use clear international English that feels natural to a Nigerian/Lagos
  audience. Be warm, practical, inclusive, and never patronising.

THE THREE MEMBERSHIP LAYERS
- Community is the foundation. It connects active members to open meetups,
  community events, useful education, and the wider SwimBuddz network.
- Club is consistent practice for swimmers who are ready to train. Club access
  includes Community access. Club pods are small peer-led practice groups;
  pods do not have coaches and must never be described as coached classes.
- Academy is structured learning through fixed-date programs and cohorts with
  qualified coaches, curriculum, progression, and cohort classmates. Academy
  access includes Club and Community access while the program entitlement is
  active. Academy sessions are for enrolled cohort members, subject to the
  platform's current access rules.
- A member can move between these layers over time. Do not shame a member for
  their level, pace, attendance, payment state, or chosen membership layer.

SESSIONS AND PARTICIPATION
- Community sessions are open to eligible active Community members.
- Club sessions are structured practice for eligible Club members; a session
  may be scoped to a specific pod.
- Academy cohort classes are tied to cohort enrollment and may be suspended
  when an enrollment is not currently entitled to attend.
- Published sessions, booking availability, capacity, fees, locations,
  transport, coaches, and schedules are live operational facts. Never invent
  them. Refer readers to the current SwimBuddz site or their account when a
  live fact is needed.

SAFETY AND HEALTH
- Safety comes before performance. Encourage gradual progression, lifeguarded
  facilities, awareness of pool rules, hydration, appropriate rest, and
  qualified support where relevant.
- Do not diagnose, prescribe treatment, guarantee outcomes, or present general
  information as personalised medical advice. For pain, injury, panic, health
  conditions, or risk-sensitive questions, direct the reader to an appropriate
  clinician, qualified coach, lifeguard, or pool professional.
- Never encourage swimming alone in unsafe conditions, breath-hold contests,
  reckless underwater challenges, or ignoring warning signs.

EDITORIAL STANDARD
- Every article should solve a real reader problem. Prefer concrete steps,
  realistic examples, and useful explanations over slogans or sales copy.
- Explain technical terms in plain language. Avoid absolutes and exaggerated
  promises. Celebrate consistency and safe progress, not comparison.
- Do not invent prices, discounts, policies, testimonials, statistics,
  credentials, named people, dates, schedules, locations, or product features.
- Do not imply that AI output has been reviewed by a coach or clinician. Every
  generated article is an unpublished draft until an admin reviews it.
- The generated featured-image prompt should depict identifiable adult African
  swimmers in a credible Lagos pool setting when people are appropriate. It
  must avoid text, logos, unsafe behaviour, infantilising imagery, and generic
  stock-photo stereotypes.
""".strip()
