import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from .db import SessionLocal
from .sync import run_sync

logger = logging.getLogger("scheduler")
scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")


def _job():
    db = SessionLocal()
    try:
        n = run_sync(db)
        logger.info("Sincronização agendada concluída: %s cargas.", n)
    except Exception:
        logger.exception("Falha na sincronização agendada.")
    finally:
        db.close()


def start_scheduler():
    if scheduler.running:
        return
    for hhmm in config.SYNC_TIMES.split(","):
        hhmm = hhmm.strip()
        if not hhmm or ":" not in hhmm:
            continue
        hh, mm = hhmm.split(":")
        scheduler.add_job(
            _job,
            CronTrigger(hour=int(hh), minute=int(mm)),
            id=f"sync-{hhmm}",
            replace_existing=True,
        )
    scheduler.start()
