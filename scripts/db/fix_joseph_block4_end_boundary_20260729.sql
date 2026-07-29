\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
    updated_rows integer;
BEGIN
    UPDATE coach_payouts
    SET period_end = TIMESTAMPTZ '2026-08-09 00:00:00+00',
        updated_at = now(),
        admin_notes = CASE
            WHEN admin_notes ILIKE '%inclusive August 8%' THEN admin_notes
            ELSE concat_ws(
                ' ',
                admin_notes,
                'Window closes 2026-08-09T00:00Z so the inclusive August 8 '
                'cohort-end class is eligible on recalculation.'
            )
        END
    WHERE id = '8fec9636-c347-4546-9277-33de0535d0f3'
      AND config_id = '733ef9ec-27e7-41c7-b9bb-5865e135b9be'
      AND block_index = 3
      AND status = 'pending'
      AND period_start = TIMESTAMPTZ '2026-07-11 00:00:00+00'
      AND period_end = TIMESTAMPTZ '2026-08-08 00:00:00+00'
      AND total_amount = 2500000;

    GET DIAGNOSTICS updated_rows = ROW_COUNT;
    IF updated_rows <> 1 THEN
        RAISE EXCEPTION
            'Expected to update one Joseph Block 4 boundary, updated %',
            updated_rows;
    END IF;
END $$;

COMMIT;

SELECT
    id,
    period_label,
    period_start,
    period_end,
    total_amount,
    status,
    admin_notes
FROM coach_payouts
WHERE id = '8fec9636-c347-4546-9277-33de0535d0f3';
