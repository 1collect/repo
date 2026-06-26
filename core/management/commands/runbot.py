import asyncio
import os
import tempfile
from html import escape

from asgiref.sync import sync_to_async
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.barcode_scanner import decode_barcode_from_image
from core.models import Transfer, User
from core.notifications import notify_transfer
from core.services import approve_transfer, sender_approve_transfer
from core.transfer_scan_service import (
    RECEIVE_ACTION,
    SHIP_ACTION,
    confirm_receive,
    confirm_shipment,
    find_active_transfers_by_barcode,
    get_product_by_barcode,
    prepare_receive_confirmation,
    prepare_shipment_confirmation,
)

TRANSFER_ACTIONS = {
    "sender_approve": sender_approve_transfer,
    "approve": approve_transfer,
}
SCAN_ACTION_LABELS = {
    SHIP_ACTION: {
        "prompt": "Пришлите фото этикетки или введите barcode для отгрузки.",
        "confirm": "Подтвердить отгрузку",
        "done": "Вы подтвердили отгрузку товара.",
    },
    RECEIVE_ACTION: {
        "prompt": "Пришлите фото этикетки или введите barcode для получения.",
        "confirm": "Подтвердить получение",
        "done": "Вы подтвердили получение товара.",
    },
}


dp = Dispatcher()


class ScanStates(StatesGroup):
    waiting_barcode = State()
    waiting_confirmation = State()


@sync_to_async
def link_user(token, telegram_id):
    user = User.objects.filter(telegram_link_token=token).first()
    if not user:
        return None
    if user.telegram_link_used_at or (
        user.telegram_link_expires_at and user.telegram_link_expires_at <= timezone.now()
    ):
        return None
    user.telegram_id = telegram_id
    user.telegram_link_used_at = timezone.now()
    user.save(update_fields=["telegram_id", "telegram_link_used_at"])
    return str(user)


@dp.message(CommandStart(deep_link=True))
async def start_link(message: Message):
    token = message.text.split(maxsplit=1)[1] if " " in message.text else ""
    try:
        name = await link_user(token, message.from_user.id)
    except Exception:
        await message.answer("Этот Telegram уже привязан к другому пользователю.")
        return
    if not name:
        await message.answer("Ссылка недействительна.")
        return
    await message.answer(f"Готово, {name}. Telegram успешно привязан к AssetChain.")


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("Откройте персональную ссылку подключения, выданную администратором.")


def _transfer_payload(transfer):
    return {
        "id": transfer.pk,
        "product": transfer.product_name_snapshot or transfer.product.name,
        "article": transfer.product_article_snapshot or transfer.product.article,
        "barcode": transfer.product_barcode_snapshot or transfer.product.barcode,
        "quantity": transfer.quantity,
        "source": transfer.source_name_snapshot or transfer.source.name,
        "destination": transfer.destination_name_snapshot or transfer.destination.name,
        "status": transfer.get_status_display(),
    }


def _card_text(payload, action):
    title = "Найдена отгрузка" if action == SHIP_ACTION else "Найдено получение"
    return (
        f"<b>{title}</b>\n\n"
        f"<b>Перемещение:</b> <b>№{payload['id']}</b>\n"
        f"<b>Товар:</b> {escape(payload['product'])}\n"
        f"<b>Артикул:</b> {escape(payload['article'])}\n"
        f"<b>Barcode:</b> {escape(payload['barcode'])}\n"
        f"<b>Количество:</b> {payload['quantity']} шт.\n"
        f"<b>Склад-отправитель:</b> {escape(payload['source'])}\n"
        f"<b>Склад-получатель:</b> {escape(payload['destination'])}\n"
        f"<b>Статус:</b> {escape(payload['status'])}"
    )


def _confirmation_markup(action, transfer_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=SCAN_ACTION_LABELS[action]["confirm"],
                    callback_data=f"scan:confirm:{action}:{transfer_id}",
                )
            ]
        ]
    )


def _selection_markup(action, transfers):
    rows = []
    for item in transfers:
        rows.append([
            InlineKeyboardButton(
                text=f"№{item['id']} {item['source']} → {item['destination']}, {item['quantity']} шт",
                callback_data=f"scan:select:{action}:{item['id']}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@sync_to_async
def perform_action(action, transfer_id, telegram_id):
    user = User.objects.filter(telegram_id=telegram_id).first()
    if not user:
        raise PermissionError("Telegram не привязан к пользователю.")
    service = TRANSFER_ACTIONS.get(action)
    if not service:
        raise ValueError("Неизвестное действие.")
    transfer = service(transfer_id, user)
    return transfer.pk, transfer.get_status_display()


@sync_to_async
def resolve_barcode_scan(telegram_id, barcode, action, preferred_transfer_id=None):
    user = User.objects.filter(telegram_id=telegram_id).first()
    if not user:
        raise PermissionError("Telegram не привязан к пользователю.")

    barcode = str(barcode or "").strip()
    product = get_product_by_barcode(barcode)
    if not product:
        return {"kind": "error", "message": "Товар с таким barcode не найден."}

    transfers = find_active_transfers_by_barcode(user, barcode, action)
    if preferred_transfer_id:
        transfers = [item for item in transfers if item.pk == preferred_transfer_id]
    if not transfers:
        return {
            "kind": "error",
            "message": "Товар найден, но активного перемещения для вашего склада не найдено.",
        }

    payloads = [_transfer_payload(item) for item in transfers]
    if len(payloads) == 1:
        return {"kind": "single", "barcode": barcode, "action": action, "transfer": payloads[0]}
    return {"kind": "multiple", "barcode": barcode, "action": action, "transfers": payloads}


@sync_to_async
def prepare_selected_transfer(telegram_id, transfer_id, barcode, action):
    user = User.objects.filter(telegram_id=telegram_id).first()
    if not user:
        raise PermissionError("Telegram не привязан к пользователю.")
    if action == SHIP_ACTION:
        result = prepare_shipment_confirmation(user, transfer_id, barcode)
    elif action == RECEIVE_ACTION:
        result = prepare_receive_confirmation(user, transfer_id, barcode)
    else:
        raise ValidationError("Неизвестное действие сканирования.")
    return _transfer_payload(result.transfer)


@sync_to_async
def confirm_scanned_transfer(telegram_id, transfer_id, barcode, action):
    user = User.objects.filter(telegram_id=telegram_id).first()
    if not user:
        raise PermissionError("Telegram не привязан к пользователю.")
    if action == SHIP_ACTION:
        transfer = confirm_shipment(user, transfer_id, barcode)
    elif action == RECEIVE_ACTION:
        transfer = confirm_receive(user, transfer_id, barcode)
    else:
        raise ValidationError("Неизвестное действие сканирования.")
    return transfer.pk, transfer.get_status_display()


async def _ask_for_barcode(callback: CallbackQuery, state: FSMContext, action, transfer_id):
    await state.set_state(ScanStates.waiting_barcode)
    await state.update_data(action=action, transfer_id=transfer_id)
    await callback.answer()
    await callback.message.answer(SCAN_ACTION_LABELS[action]["prompt"])


@dp.callback_query(F.data.startswith("transfer:"))
async def transfer_callback(callback: CallbackQuery, state: FSMContext):
    _, action, raw_id = callback.data.split(":")
    transfer_id = int(raw_id)
    if action in {SHIP_ACTION, RECEIVE_ACTION}:
        await _ask_for_barcode(callback, state, action, transfer_id)
        return
    try:
        transfer_id, status = await perform_action(action, transfer_id, callback.from_user.id)
    except Exception as error:
        await callback.answer(str(error), show_alert=True)
        return
    await callback.answer("Операция выполнена")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"<b>Перемещение №{transfer_id}</b>: {status}", parse_mode="HTML")
    transfer = await sync_to_async(Transfer.objects.get)(pk=transfer_id)
    await sync_to_async(notify_transfer)(transfer)


@dp.callback_query(F.data.startswith("scan:request:"))
async def scan_request_callback(callback: CallbackQuery, state: FSMContext):
    _, _, action, raw_id = callback.data.split(":")
    if action not in SCAN_ACTION_LABELS:
        await callback.answer("Неизвестное действие.", show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _ask_for_barcode(callback, state, action, int(raw_id))


async def _show_scan_result(message: Message, state: FSMContext, barcode):
    data = await state.get_data()
    action = data.get("action")
    transfer_id = data.get("transfer_id")
    if action not in SCAN_ACTION_LABELS:
        await message.answer("Сначала нажмите кнопку “Отгрузить” или “Получить” в сообщении перемещения.")
        return
    result = await resolve_barcode_scan(message.from_user.id, barcode, action, transfer_id)
    if result["kind"] == "error":
        await message.answer(result["message"])
        return
    await state.update_data(barcode=result["barcode"])
    if result["kind"] == "multiple":
        await message.answer(
            "Найдено несколько подходящих перемещений. Выберите нужное:",
            reply_markup=_selection_markup(action, result["transfers"]),
        )
        return
    payload = result["transfer"]
    await state.set_state(ScanStates.waiting_confirmation)
    await state.update_data(transfer_id=payload["id"], action=action)
    await message.answer(
        _card_text(payload, action),
        reply_markup=_confirmation_markup(action, payload["id"]),
        parse_mode="HTML",
    )


@dp.message(ScanStates.waiting_barcode, F.photo)
async def scan_photo(message: Message, state: FSMContext, bot: Bot):
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
        temp_path = temp_file.name
    try:
        await bot.download(message.photo[-1], destination=temp_path)
        barcode = await sync_to_async(decode_barcode_from_image)(temp_path)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    if not barcode:
        await message.answer("Не удалось считать штрихкод. Попробуйте отправить фото ближе и чётче или введите код вручную.")
        return
    await _show_scan_result(message, state, barcode)


@dp.message(ScanStates.waiting_barcode, F.text)
async def scan_text_barcode(message: Message, state: FSMContext):
    barcode = (message.text or "").strip()
    if not barcode:
        await message.answer("Введите barcode цифрами или отправьте фото этикетки.")
        return
    await _show_scan_result(message, state, barcode)


@dp.callback_query(F.data.startswith("scan:select:"))
async def scan_select_callback(callback: CallbackQuery, state: FSMContext):
    _, _, action, raw_id = callback.data.split(":")
    data = await state.get_data()
    barcode = data.get("barcode")
    if not barcode:
        await callback.answer("Barcode потерян. Отправьте фото или код заново.", show_alert=True)
        await state.set_state(ScanStates.waiting_barcode)
        return
    try:
        payload = await prepare_selected_transfer(callback.from_user.id, int(raw_id), barcode, action)
    except Exception as error:
        await callback.answer(str(error), show_alert=True)
        return
    await state.set_state(ScanStates.waiting_confirmation)
    await state.update_data(action=action, transfer_id=payload["id"], barcode=barcode)
    await callback.answer()
    await callback.message.edit_text(
        _card_text(payload, action),
        reply_markup=_confirmation_markup(action, payload["id"]),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("scan:confirm:"))
async def scan_confirm_callback(callback: CallbackQuery, state: FSMContext):
    _, _, action, raw_id = callback.data.split(":")
    data = await state.get_data()
    barcode = data.get("barcode")
    if not barcode:
        await callback.answer("Barcode потерян. Отправьте фото или код заново.", show_alert=True)
        await state.set_state(ScanStates.waiting_barcode)
        return
    try:
        transfer_id, status = await confirm_scanned_transfer(
            callback.from_user.id,
            int(raw_id),
            barcode,
            action,
        )
    except Exception as error:
        await callback.answer(str(error), show_alert=True)
        return
    await state.clear()
    await callback.answer("Операция выполнена")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"<b>Перемещение №{transfer_id}</b>: {status}", parse_mode="HTML")
    transfer = await sync_to_async(Transfer.objects.get)(pk=transfer_id)
    await sync_to_async(notify_transfer)(transfer)


class Command(BaseCommand):
    help = "Запускает Telegram-бота AssetChain"

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("Укажите TELEGRAM_BOT_TOKEN.")

        async def main():
            bot = Bot(settings.TELEGRAM_BOT_TOKEN)
            await dp.start_polling(bot)

        asyncio.run(main())