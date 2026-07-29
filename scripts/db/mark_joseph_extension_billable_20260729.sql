\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
    updated_rows integer;
    matching_payouts integer;
BEGIN
    SELECT COUNT(*)
    INTO matching_payouts
    FROM coach_payouts
    WHERE config_id = '733ef9ec-27e7-41c7-b9bb-5865e135b9be'
      AND block_index = 3
      AND period_start = TIMESTAMPTZ '2026-07-11 00:00:00+00'
      AND period_end = TIMESTAMPTZ '2026-08-09 00:00:00+00'
      AND total_amount = 2500000
      AND status = 'pending';

    IF matching_payouts <> 1 THEN
        RAISE EXCEPTION
            'Expected one pending 2500000-kobo Joseph extension payout, found %',
            matching_payouts;
    END IF;

    UPDATE cohort_extension_requests
    SET coach_payout_billable = true,
        coach_payout_synced_at = now(),
        admin_notes = CASE
            WHEN admin_notes ILIKE '%coach payout billable%' THEN admin_notes
            ELSE concat_ws(
                ' | ',
                admin_notes,
                '2026-07-29: coach payout billable; Block 4 reconciled.'
            )
        END
    WHERE id = '72b0838b-0c9a-4570-91f0-f7d156dff432'
      AND cohort_id = 'fd868184-9e2f-46ad-a33b-9d0fdff54089'
      AND status = 'approved'
      AND current_end_date = TIMESTAMPTZ '2026-07-11 00:00:00+00'
      AND proposed_end_date = TIMESTAMPTZ '2026-08-08 00:00:00+00';

    GET DIAGNOSTICS updated_rows = ROW_COUNT;
    IF updated_rows <> 1 THEN
        RAISE EXCEPTION
            'Expected to mark one Joseph extension request billable, updated %',
            updated_rows;
    END IF;
END $$;

COMMIT;

SELECT
    id,
    cohort_id,
    status,
    coach_payout_billable,
    coach_payout_synced_at,
    current_end_date,
    proposed_end_date,
    admin_notes
FROM cohort_extension_requests
WHERE id = '72b0838b-0c9a-4570-91f0-f7d156dff432';
