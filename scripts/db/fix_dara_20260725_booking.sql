\set ON_ERROR_STOP on

-- Reallocate Dara's PAY-87123 entitlement from the Aug 1 cohort class to the
-- Jul 25 class she attended. The successful transfer landed at 12:21 WAT on
-- Jul 25, after her earlier Jul 25 pending booking had expired; the UI then
-- attached the payment to Aug 1 and created a future PRESENT attendance row.
--
-- The precondition block makes this fail closed if production state has moved.
-- No money or ledger entry changes: the same ₦3,500 session-booking revenue
-- remains recognised under PAY-87123. Only its session entitlement is moved.

BEGIN;

DO $$
DECLARE
    old_booking_status text;
    future_booking_status text;
    payment_status text;
    attendance_session uuid;
BEGIN
    SELECT status::text
      INTO old_booking_status
      FROM session_bookings
     WHERE id = 'da480075-a71f-421b-91cb-1bcc0b1e1821';

    SELECT status::text
      INTO future_booking_status
      FROM session_bookings
     WHERE id = 'eaefe85f-7c24-46d3-a76f-244220fd9f39';

    SELECT status::text
      INTO payment_status
      FROM payments
     WHERE id = '3231ab6e-1d95-42ec-b2da-ca82ba2221f1'
       AND session_booking_id = 'eaefe85f-7c24-46d3-a76f-244220fd9f39';

    SELECT session_id
      INTO attendance_session
      FROM attendance_records
     WHERE id = 'b57bb49e-ae2b-4381-9cfa-de5ddcd0afdc';

    IF old_booking_status IS DISTINCT FROM 'expired'
       OR future_booking_status IS DISTINCT FROM 'confirmed'
       OR payment_status IS DISTINCT FROM 'paid'
       OR attendance_session IS DISTINCT FROM
          'ebc82354-e773-4064-b1c0-d799cf52ed4a'::uuid
    THEN
        RAISE EXCEPTION
          'Dara correction precondition failed (old=%, future=%, payment=%, attendance_session=%)',
          old_booking_status,
          future_booking_status,
          payment_status,
          attendance_session;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM attendance_records
         WHERE session_id = 'd0bf00c9-bbe7-4f60-9735-a70d0a118a02'
           AND member_id = '84ee485c-dee9-45e1-acc3-777c68b8d2d5'
    ) THEN
        RAISE EXCEPTION 'Dara already has Jul 25 attendance; refusing to duplicate';
    END IF;
END
$$;

UPDATE session_bookings
   SET status = 'confirmed',
       payment_intent_id = '3231ab6e-1d95-42ec-b2da-ca82ba2221f1',
       confirmed_at = '2026-07-25 11:21:34.093129+00',
       cancelled_at = NULL,
       expires_at = NULL,
       notes = 'Ops correction 2026-07-28: PAY-87123 reallocated from Aug 1 to attended Jul 25 class',
       updated_at = now()
 WHERE id = 'da480075-a71f-421b-91cb-1bcc0b1e1821';

UPDATE session_bookings
   SET status = 'cancelled',
       payment_intent_id = NULL,
       cancelled_at = now(),
       notes = 'Ops correction 2026-07-28: PAY-87123 moved to attended Jul 25 class; no refund',
       updated_at = now()
 WHERE id = 'eaefe85f-7c24-46d3-a76f-244220fd9f39';

UPDATE payments
   SET session_booking_id = 'da480075-a71f-421b-91cb-1bcc0b1e1821',
       metadata = jsonb_set(
           jsonb_set(
               metadata,
               '{booking_id}',
               to_jsonb('da480075-a71f-421b-91cb-1bcc0b1e1821'::text)
           ),
           '{session_id}',
           to_jsonb('d0bf00c9-bbe7-4f60-9735-a70d0a118a02'::text)
       ),
       updated_at = now()
 WHERE id = '3231ab6e-1d95-42ec-b2da-ca82ba2221f1';

UPDATE attendance_records
   SET session_id = 'd0bf00c9-bbe7-4f60-9735-a70d0a118a02',
       booking_id = 'da480075-a71f-421b-91cb-1bcc0b1e1821',
       status = 'present',
       notes = 'Ops correction 2026-07-28: attended Jul 25; PAY-87123 received 12:21 WAT',
       updated_at = now()
 WHERE id = 'b57bb49e-ae2b-4381-9cfa-de5ddcd0afdc';

COMMIT;

SELECT p.reference,
       p.status,
       p.session_booking_id,
       p.metadata->>'session_id' AS paid_session_id,
       sb.status AS booking_status,
       s.title,
       s.starts_at AT TIME ZONE 'Africa/Lagos' AS starts_wat,
       ar.status AS attendance_status
  FROM payments p
  JOIN session_bookings sb ON sb.id = p.session_booking_id
  JOIN sessions s ON s.id = sb.session_id
  LEFT JOIN attendance_records ar
    ON ar.booking_id = sb.id
   AND ar.member_id = sb.member_id
 WHERE p.reference = 'PAY-87123';
