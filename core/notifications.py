import asyncio
import logging
from html import escape
from asgiref.sync import async_to_sync
from django.conf import settings
from django.db.models import Q
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from .models import Transfer, User

logger = logging.getLogger(__name__)


def _recipient_and_buttons(transfer):
    if transfer.status == Transfer.Status.WAITING_SENDER_APPROVAL:
        return transfer.source.responsible, [
            ("Подтвердить согласие", f"transfer:sender_approve:{transfer.pk}"),
        ]
    if transfer.status == Transfer.Status.WAITING_ADMIN_APPROVAL:
        return None, [
            ("Согласовать", f"transfer:approve:{transfer.pk}"),
        ]
    if transfer.status == Transfer.Status.APPROVED:
        return transfer.source.responsible, [
            ("Отгрузить", f"scan:request:ship:{transfer.pk}")
        ]
    if transfer.status == Transfer.Status.IN_TRANSIT:
        return transfer.destination.responsible, [
            ("Получить", f"scan:request:receive:{transfer.pk}")
        ]
    return None, []


def _active_admins():
    return User.objects.filter(is_active=True).filter(
        Q(role=User.Role.ADMIN) | Q(is_superuser=True)
    )


def _active_responsibles():
    return User.objects.filter(is_active=True, role=User.Role.RESPONSIBLE)


def _warehouse_responsibles(warehouse):
    if warehouse.responsible:
        return [warehouse.responsible]
    return list(_active_responsibles())


def _unique_users(*users):
    unique = {}
    for user in users:
        if user and user.telegram_id:
            unique[user.pk] = user
    return list(unique.values())


async def _send(telegram_id, text, buttons):
    bot = Bot(settings.TELEGRAM_BOT_TOKEN)
    try:
        markup = None
        if buttons:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=label, callback_data=callback_data)]
                    for label, callback_data in buttons
                ]
            )
        await bot.send_message(telegram_id, text, reply_markup=markup, parse_mode="HTML")
    finally:
        await bot.session.close()


def send_telegram_message(telegram_id, text, buttons=None):
    if not settings.TELEGRAM_BOT_TOKEN or not telegram_id:
        return False
    try:
        async_to_sync(_send)(telegram_id, text, buttons or [])
    except Exception:
        logger.exception("Не удалось отправить Telegram-сообщение")
        return False
    return True


def notify_transfer(transfer):
    if not settings.TELEGRAM_BOT_TOKEN:
        return
    transfer = Transfer.objects.select_related(
        "source__responsible", "destination__responsible", "created_by", "product"
    ).get(pk=transfer.pk)
    recipient, buttons = _recipient_and_buttons(transfer)
    recipients = []
    prefix = ""
    if transfer.status == Transfer.Status.WAITING_SENDER_APPROVAL:
        recipients = _unique_users(*(_warehouse_responsibles(transfer.source) if recipient is None else [recipient]))
        prefix = "Создана заявка на перемещение с вашего склада. Подтвердите согласие на отправку.\n\n"
    elif transfer.status == Transfer.Status.WAITING_ADMIN_APPROVAL:
        recipients = _unique_users(*_active_admins())
        prefix = "Отправитель согласовал заявку. Требуется согласование админа.\n\n"
    elif transfer.status == Transfer.Status.SENDER_REJECTED:
        recipients = _unique_users(transfer.created_by, *_active_admins())
        prefix = f"Отправитель отказал в заявке. Причина: {escape(transfer.sender_rejection_reason)}\n\n"
    elif transfer.status == Transfer.Status.APPROVED:
        recipients = _unique_users(*(_warehouse_responsibles(transfer.source) if recipient is None else [recipient]))
        prefix = "Заявка согласована. Теперь можно отметить товар как отгруженный.\n\n"
    elif transfer.status == Transfer.Status.IN_TRANSIT:
        recipients = _unique_users(*(_warehouse_responsibles(transfer.destination) if recipient is None else [recipient]))
        prefix = "Товар отгружен. Подтвердите получение после доставки.\n\n"
    elif transfer.status == Transfer.Status.COMPLETED:
        recipients = _unique_users(*_warehouse_responsibles(transfer.source), *_active_admins())
        prefix = "Товар получен. Перемещение завершено.\n\n"
    elif transfer.status == Transfer.Status.ADMIN_REJECTED:
        recipients = _unique_users(transfer.created_by, *_warehouse_responsibles(transfer.source))
        prefix = f"Заявка отклонена админом. Причина: {escape(transfer.rejection_reason)}\n\n"
    if not recipients:
        return
    source_name = escape(transfer.source_name_snapshot or transfer.source.name)
    destination_name = escape(transfer.destination_name_snapshot or transfer.destination.name)
    product_name = escape(transfer.product_name_snapshot or transfer.product.name)
    article = escape(transfer.product_article_snapshot or transfer.product.article)
    barcode = escape(transfer.product_barcode_snapshot or transfer.product.barcode)
    reason = escape(transfer.reason)
    text = (
        prefix +
        f"<b>Перемещение №{transfer.pk}</b>\n\n"
        f"<b>Со склада:</b> {source_name}\n"
        f"<b>На склад:</b> {destination_name}\n"
        f"<b>Товар:</b> {product_name}\n"
        f"<b>Артикул:</b> {article}\n"
        f"<b>Штрихкод:</b> {barcode}\n"
        f"<b>Количество:</b> {transfer.quantity}\n"
        f"<b>Причина:</b> {reason}\n"
        f"<b>Статус:</b> {escape(transfer.get_status_display())}"
    )
    for user in recipients:
        send_telegram_message(user.telegram_id, text, buttons if recipient is None or user == recipient else [])


def notify_transfer_action_confirmed(user, transfer, action):
    labels = {
        "ship": "Вы подтвердили отгрузку товара.",
        "receive": "Вы подтвердили получение товара.",
    }
    text = labels.get(action)
    if not text:
        return False
    return send_telegram_message(
        user.telegram_id,
        f"{text}\n\nПеремещение №{transfer.pk}: {transfer.product_name_snapshot or transfer.product.name}",
    )


def notify_telegram_unlink(user, changed_by):
    return send_telegram_message(
        user.telegram_id,
        (
            "Ваш Telegram отвязывают от AssetChain.\n\n"
            f"Учётная запись: {user}\n"
            f"Администратор: {changed_by}\n"
            "Для повторного подключения запросите новую персональную ссылку."
        ),
    )


def notify_telegram_account_deactivated(telegram_id):
    return send_telegram_message(
        telegram_id,
        (
            "Ваш Telegram-аккаунт отвязан от системы, потому что пользователь больше не активен. "
            "Если это ошибка, обратитесь к администратору."
        ),
    )


def notify_warehouse_responsible_changed(warehouse, old_responsible, new_responsible, changed_by):
    if old_responsible and old_responsible.telegram_id:
        send_telegram_message(
            old_responsible.telegram_id,
            f"Вы больше не назначены ответственным за склад: <b>{escape(warehouse.name)}</b>.",
        )
    if new_responsible and new_responsible.telegram_id:
        send_telegram_message(
            new_responsible.telegram_id,
            f"Вы назначены ответственным за склад: <b>{escape(warehouse.name)}</b>.",
        )


def notify_user_access_change(user, changed_by, changes):
    return send_telegram_message(
        user.telegram_id,
        (
            "Изменены настройки вашей учётной записи AssetChain.\n\n"
            + "\n".join(f"• {change}" for change in changes)
            + f"\n\nАдминистратор: {changed_by}"
        ),
    )


def reminder_recipients(transfer):
    recipients = []
    if transfer.status == Transfer.Status.WAITING_SENDER_APPROVAL:
        recipients.extend(_warehouse_responsibles(transfer.source))
    elif transfer.status == Transfer.Status.WAITING_ADMIN_APPROVAL:
        recipients.extend(_active_admins())
    elif transfer.status == Transfer.Status.APPROVED:
        recipients.extend(_warehouse_responsibles(transfer.source))
    elif transfer.status == Transfer.Status.IN_TRANSIT:
        recipients.extend(_warehouse_responsibles(transfer.destination))
    recipients.extend(
        User.objects.filter(is_active=True).filter(
            Q(role=User.Role.ADMIN) | Q(is_superuser=True)
        )
    )

    unique = {}
    for user in recipients:
        if user and user.telegram_id:
            unique[user.pk] = user
    return list(unique.values())


def notify_transfer_reminder(transfer):
    text = (
        f"Напоминание по <b>перемещению №{transfer.pk}</b>\n\n"
        f"{transfer.product_name_snapshot or transfer.product.name}\n"
        f"Количество: {transfer.quantity}\n"
        f"{transfer.source_name_snapshot or transfer.source.name} → "
        f"{transfer.destination_name_snapshot or transfer.destination.name}\n"
        f"Текущий статус: {transfer.get_status_display()}\n\n"
        "Заявка ожидает действия."
    )
    _, buttons = _recipient_and_buttons(transfer)
    sent = 0
    for user in reminder_recipients(transfer):
        if send_telegram_message(user.telegram_id, text, buttons):
            sent += 1
    return sent
