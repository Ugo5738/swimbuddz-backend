from services.communications_service.worker import WorkerSettings


def _cron_job(name: str):
    return next(job for job in WorkerSettings.cron_jobs if job.name == name)


def test_weekly_digest_runs_sunday_at_noon_wat():
    job = _cron_job("cron:task_send_weekly_session_digest")

    assert job.weekday == 6
    assert job.hour == 11  # 12:00 WAT
    assert job.minute == 0


def test_booking_prompts_run_tuesday_thursday_and_friday():
    job = _cron_job("cron:task_send_session_booking_prompts")

    assert job.weekday == {1, 3, 4}
    assert job.hour == 8  # 09:00 WAT
    assert job.minute == 0
