from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .models import Product, Transfer, User
from .services import receive_transfer, ship_transfer

SHIP_ACTION = "ship"
RECEIVE_ACTION = "receive"
SHIP_STATUSES = [Transfer.Status.APPROVED]
RECEIVE_STATUSES = [Transfer.Status.IN_TRANSIT, Transfer.Status.SHIPPED]


@dataclass(frozen=True)
class TransferScanResult:
    transfer: Transfer
    action: str
    barcode: str


def _normalize_barcode(barcode):
    return str(barcode or "").strip()


def _can_manage_warehouse(user, warehouse):
    return (
        user.is_main_admin
        or warehouse.responsible_id == user.id
        or (warehouse.responsible_id is None and user.role == User.Role.RESPONSIBLE)
    )


def get_product_by_barcode(barcode):
    barcode = _normalize_barcode(barcode)
    if not barcode:
        return None
    return Product.objects.filter(barcode=barcode, is_active=True).first()


def find_active_transfers_by_barcode(user, barcode, action):
    barcode = _normalize_barcode(barcode)
    product = get_product_by_barcode(barcode)
    if not product:
        return []

    transfers = Transfer.objects.select_related(
        "source", "destination", "product", "source__responsible", "destination__responsible"
    ).filter(product=product).order_by("created_at")

    if action == SHIP_ACTION:
        transfers = transfers.filter(status__in=SHIP_STATUSES)
        if not user.is_main_admin:
            transfers = transfers.filter(source__responsible=user)
    elif action == RECEIVE_ACTION:
        transfers = transfers.filter(status__in=RECEIVE_STATUSES)
        if not user.is_main_admin:
            transfers = transfers.filter(destination__responsible=user)
    else:
        raise ValidationError("Неизвестное действие сканирования.")

    return list(transfers)


def _get_transfer_for_confirmation(user, transfer_id, barcode, action):
    barcode = _normalize_barcode(barcode)
    transfer = Transfer.objects.select_related("source", "destination", "product").get(pk=transfer_id)
    if transfer.product.barcode != barcode:
        raise ValidationError("Штрихкод не совпадает с товаром в перемещении.")
    if action == SHIP_ACTION:
        if transfer.status not in SHIP_STATUSES:
            raise ValidationError("Перемещение не ожидает отгрузки.")
        if not _can_manage_warehouse(user, transfer.source):
            raise PermissionDenied("Подтвердить отгрузку может ответственный склада-отправителя.")
    elif action == RECEIVE_ACTION:
        if transfer.status not in RECEIVE_STATUSES:
            raise ValidationError("Перемещение не находится в пути.")
        if not _can_manage_warehouse(user, transfer.destination):
            raise PermissionDenied("Подтвердить получение может ответственный склада-получателя.")
    else:
        raise ValidationError("Неизвестное действие сканирования.")
    return TransferScanResult(transfer=transfer, action=action, barcode=barcode)


def prepare_shipment_confirmation(user, transfer_id, barcode):
    return _get_transfer_for_confirmation(user, transfer_id, barcode, SHIP_ACTION)


def prepare_receive_confirmation(user, transfer_id, barcode):
    return _get_transfer_for_confirmation(user, transfer_id, barcode, RECEIVE_ACTION)


@transaction.atomic
def confirm_shipment(user, transfer_id, barcode=None):
    if barcode:
        prepare_shipment_confirmation(user, transfer_id, barcode)
    return ship_transfer(transfer_id, user, scanned_barcode=_normalize_barcode(barcode))


@transaction.atomic
def confirm_receive(user, transfer_id, barcode=None):
    if barcode:
        prepare_receive_confirmation(user, transfer_id, barcode)
    return receive_transfer(transfer_id, user, scanned_barcode=_normalize_barcode(barcode))