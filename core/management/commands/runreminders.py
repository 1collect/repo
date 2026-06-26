import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.models import Transfer
from core.notifications import notify_transfer_reminder


def reminder_due_at(transfer):
    if transfer.status == Transfer.Status.WAITING_SENDER_APPROVAL:
        return transfer.created_at
    if transfer.status == Transfer.Status.WAITING_ADMIN_APPROVAL:
        return transfer.sender_approved_at or transfer.created_at
    if transfer.status == Transfer.Status.APPROVED:
        return transfer.approved_at or transfer.created_at
    if transfer.status == Transfer.Status.IN_TRANSIT:
        return transfer.shipped_at or transfer.created_at
    return None


def send_due_reminders():
    now = timezone.now()
    delay = timezone.timedelta(hours=settings.TRANSFER_REMINDER_AFTER_HOURS)
    repeat = timezone.timedelta(hours=settings.TRANSFER_REMINDER_REPEAT_HOURS)
    transfers = Transfer.objects.filter(
        status__in=[
            Transfer.Status.WAITING_SENDER_APPROVAL,
            Transfer.Status.WAITING_ADMIN_APPROVAL,
            Transfer.Status.APPROVED,
            Transfer.Status.IN_TRANSIT,
        ]
    ).select_related(
        "source__responsible",
        "destination__responsible",
        "product",
    )

    sent_for = 0
    for transfer in transfers:
        started_at = reminder_due_at(transfer)
        if not started_at or now - started_at < delay:
            continue
        if transfer.last_reminded_at and now - transfer.last_reminded_at < repeat:
            continue
        notify_transfer_reminder(transfer)
        transfer.last_reminded_at = now
        transfer.save(update_fields=["last_reminded_at"])
        sent_for += 1
    return sent_for


class Command(BaseCommand):
    help = "Отправляет Telegram-напоминания по перемещениям, ожидающим действия"

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        while True:
            sent_for = send_due_reminders()
            if sent_for:
                self.stdout.write(f"Обработано напоминаний: {sent_for}")
            if not options["loop"]:
                break
            time.sleep(settings.TRANSFER_REMINDER_CHECK_SECONDS)
