# Chat Safeguarding Policy — Version 1.0

> **Status:** DRAFT — pending review by Daniel (SwimBuddz owner/safeguarding admin)
> **Effective date:** TBD on approval
> **Version:** 1.0
> **Agreement type:** `safeguarding` (in `agreement_versions` table)
> **Last updated:** 2026-04-20

> **Note:** This is a working draft produced without specialist legal/safeguarding
> review. It is intended as a starting point that covers the obvious bases so the
> technical infrastructure can be built against a real document. Before any user
> is asked to accept this version, it must be reviewed by (at minimum) the
> SwimBuddz owner and ideally a child-safeguarding professional familiar with
> Nigerian child-protection norms.

---

## 1. Purpose

SwimBuddz provides in-app messaging to support coaching, training, community life, events, and transport coordination. A significant proportion of our members are minors. This policy sets the standards everyone must follow when using SwimBuddz chat, with a particular focus on protecting young people.

Acceptance of this policy is a condition of using SwimBuddz chat features. It applies to all roles — members, parents/guardians, coaches, volunteers, and administrators.

## 2. Who this covers

- **Members**, including minors and adults, who use any chat surface in SwimBuddz.
- **Parents and guardians** linked to minor members via a verified `GuardianLink`.
- **Coaches**, who have additional responsibilities under §6.
- **Volunteers and staff** who interact with members through chat.
- **Administrators**, including Platform Admins and Safeguarding Admins.

## 3. Core principles

1. **Children's welfare is paramount.** Any judgement call in chat defaults to the interpretation that best protects a young person.
2. **Transparency over secrecy.** No secret channels involving minors. Conversations touching minors can be reviewed by Safeguarding Admins.
3. **Proportionate trust.** Access to chat features scales with verification level (e.g. a verified guardian has privileges an unverified one does not).
4. **Report, don't engage.** If you see something that concerns you, use the in-app report tool rather than confronting the person yourself.
5. **Consequences match intent.** Honest mistakes are handled differently from deliberate harm, but both are logged.

## 4. Expected behaviour

Everyone using SwimBuddz chat agrees to:

- Treat others with respect — no harassment, discrimination, hate speech, bullying, or personal attacks.
- Keep content appropriate for a mixed-age community. No sexual content, violence, or material glorifying self-harm or substance abuse.
- Only share images and video you have the right to share, where every identifiable person has consented (and where minors are involved, their guardian has consented).
- Respect SwimBuddz's boundaries — chat is for swimming community life, not for unrelated solicitation, sales, or proselytising.
- Use your real identity. Impersonation is prohibited.

## 5. Prohibited conduct

The following will lead to immediate action, including removal from chat and possible reporting to authorities:

- Any sexual content, sexualised language, or grooming behaviour directed at a minor. This is the most serious category and is always investigated.
- Soliciting personal contact details (phone number, home address, social media handles) from a minor outside of legitimate SwimBuddz operational needs, and doing so without the minor's guardian present or aware.
- Arranging to meet a minor outside scheduled SwimBuddz activities, without guardian knowledge.
- Sharing child sexual abuse material of any kind. This is reported to Nigerian authorities.
- Doxxing, sharing private information about another member without consent.
- Threats of violence, self-harm content glamorising or encouraging self-harm.
- Selling, trading, or promoting illegal goods, services, or substances.

## 6. Rules specifically for coaches

Coaches carry additional duties because of their position of trust:

1. **No 1:1 direct messages with a minor.** Every chat between a coach and a minor must include the minor's verified guardian, or take place in a group channel (cohort, pod, etc.) with multiple people present. This rule is enforced by SwimBuddz systems and cannot be worked around.
2. **No off-platform chat with minors.** Do not move conversations about SwimBuddz matters to WhatsApp, Telegram, Instagram DMs, or any other channel where it is not logged and moderated. If a minor or parent initiates off-platform contact, politely redirect to SwimBuddz chat.
3. **No secret meetings.** Any face-to-face contact with a minor outside a scheduled SwimBuddz session requires the guardian's prior explicit consent, logged (e.g. via a chat message in the coach-parent channel).
4. **Transparency in photos.** If a coach takes a photo that includes a minor, the minor's guardian must be able to view it on request. Photos intended for marketing require a separate consent via the media consent flow.
5. **Report concerns fast.** If a coach sees something that worries them about a young person — in chat or in person — they raise it to a Safeguarding Admin within 24 hours, using the in-app report flow or a direct message to the safeguarding team.

## 7. Rules for parents and guardians

1. A verified `GuardianLink` is required to exercise guardian rights in chat (e.g. being added to coach-minor DMs).
2. Guardians are expected to supervise their minor's use of SwimBuddz chat and to read the chat they are a party to.
3. Guardians do not share their account with their minor. Each account is personal.
4. Guardians must report safeguarding concerns through the in-app report flow or by messaging the safeguarding team.

## 8. Content moderation

- SwimBuddz uses automated content moderation for text (OpenAI Moderation) and images (AWS Rekognition). These systems produce confidence scores, not verdicts, and regularly false-positive on legitimate swim content (children in swimwear at pools). No content is auto-deleted. Flagged content is held for human review by a Safeguarding Admin.
- Moderation thresholds are configurable by Safeguarding Admins and are tuned for the swim context.
- Humans, not automated systems, make the final call on whether content is acceptable.

## 9. Reporting

If you see something that breaks these rules:

1. Use the **Report** action on the specific message (or member).
2. Choose the most accurate reason: **Safeguarding**, **Harassment**, **Spam**, or **Other**.
3. Add a note if it helps explain the report.
4. You will not be identified to the reported member.

Reports involving a minor go to a dedicated Safeguarding Admin queue with a stricter response-time target. Retaliating against a reporter is itself prohibited.

## 10. Enforcement

Depending on severity and intent, SwimBuddz may take any combination of:

- A private warning through chat.
- A temporary mute on a channel or account.
- Removal from a specific channel.
- Removal from SwimBuddz chat entirely.
- Removal from SwimBuddz membership.
- Reporting to external authorities (police, child protection agencies), where the behaviour is potentially criminal. SwimBuddz will cooperate fully with lawful requests for information from authorities.

## 11. Audit and data retention

- Every chat message, edit, delete, report, and moderation action is logged in a tamper-resistant audit trail, retained indefinitely for safeguarding purposes.
- Messages in channels containing minors cannot be hard-deleted by anyone except a Safeguarding Admin, and only after the retention window has expired.
- Audit records are kept even if a channel is archived or a member leaves SwimBuddz.

## 12. Data rights

SwimBuddz complies with applicable data-protection law. Members and guardians can request access to or correction of their personal data by contacting support. Safeguarding records may be retained beyond normal retention periods where there is a legitimate safeguarding reason, and such retention is itself recorded.

## 13. Changes to this policy

When this policy is updated, a new version is registered in `agreement_versions` with `agreement_type = 'safeguarding'`, the new text, and a fresh SHA-256 hash. Members are required to accept the new version; continued use of chat after the effective date of a new version indicates acceptance unless the policy update is material, in which case active re-acceptance is required.

## 14. Contact

Concerns, questions, or reports that cannot go through the in-app tool:

- SwimBuddz support (via your in-app account area).
- Safeguarding team — direct contact details published separately to avoid defunct contacts being baked into this versioned document.

---

## Acceptance

By using SwimBuddz chat, you confirm that you have read and agree to the current version of this policy as registered in the `agreement_versions` table.

---

**Internal notes (not part of the accepted policy — strip before publishing to users):**

- This draft is intentionally accessible in tone — readable by a 14-year-old and by an adult guardian with limited English-medium legal reading.
- §5 and §6 contain the bits safeguarding professionals will scrutinise first; update those with specialist input before launch.
- §12 "Data rights" should be cross-referenced with the SwimBuddz Privacy Policy once that exists. Currently a stub.
- §14 "Contact" deliberately does not name a person — that belongs in a separate operational runbook, not in a versioned document that must be re-accepted when contact people change.
