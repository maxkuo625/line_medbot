from apscheduler.schedulers.background import BackgroundScheduler
from medication_reminder import run_reminders

scheduler = BackgroundScheduler()
scheduler_started = False

def start_scheduler(line_bot_api):
    global scheduler_started
    if not scheduler_started:
        scheduler.add_job(lambda: run_reminders(line_bot_api), 'cron', minute='*')
        scheduler.start()
        scheduler_started = True
