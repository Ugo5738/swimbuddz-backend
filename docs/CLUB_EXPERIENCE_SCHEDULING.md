# Club scheduling and Community Experience implementation contract

This extends the deployed Club/Academy contract. Annual Membership, Club,
Academy, and Community Experience remain separate products. Existing purchases,
payment snapshots, safety assessment rules, transition session pricing, and
Academy commercial snapshots must remain intact.

## Agreed changes

1. Club is location-specific. A different home pool/cost base uses a different
   Club location; pods belong to that location, not a separate pricing tier.
2. A quarter is an individually published commercial version. Reuse a previous
   quarter to generate a draft, never an automatically published subscription.
3. Link plans to actual Club Sessions. Session cost-plus pricing already owns
   pool, refreshments, other costs, expected attendance and margin. Sum the
   selected sessions' commercial prices for the recommended quarter price;
   allow an explicit final-price override and snapshot both. No global ₦5,000.
4. Entry/proration uses actual eligible, not-yet-started linked sessions, not
   weekday arithmetic. Cancelled/replaced sessions do not count. A final-price
   override is prorated using the linked sessions' frozen commercial weights.
   Already-purchased quarters/payment amounts are never rewritten.
5. Community Experience owns its package/ticket prices. Events own dates,
   logistics, venue and cost planning. Link one or multiple Events, including
   multi-day Events, without allowing a second standalone Event checkout.
6. Experience/Club scheduling impact is explicit: separate, parallel, or replaces
   specified Club sessions. Separate/parallel leave swims available for people
   not attending Experience. Q4's suggested wrap-up is the first Saturday of
   December (December 5 in 2026), not the quarter end. Admin reviews the dates.
7. Member, Club member and quarterly Club-bundle prices remain distinct.
   Admin configures guest/+1 and public-guest prices and limits per offering.
   Record each participant, waiver and paid amount separately. Public guests do
   not need annual Membership; they cannot enter their own price. Capacity must
   include held and confirmed participants, with idempotent payment fulfillment.
8. Club checkout exposes the existing bank-transfer option. A transfer already
   made is reconciled with proof and Admin approval of the server quote, not a
   second transfer or a direct enrollment insertion. Readiness is still required.

## Verification boundaries

Use non-database unit/component tests, TypeScript, lint, OpenAPI freshness,
production compilation, and offline migration SQL checks locally. PostgreSQL
integration tests are written for CI; Docker and production data are out of scope.
No automatic production configuration, paid booking cancellation, refund, or
branch cleanup is authorized by this implementation.
