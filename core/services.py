from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import OperationLog, Product, Stock, Transfer, User


def generate_article():
    used = set(Product.objects.values_list("article", flat=True))
    for number in range(1, 100000):
        article = f"{number:05d}"
        if article not in used:
            return article
    raise ValidationError("Свободные пятизначные артикулы закончились.")


def calculate_ean13_checksum(base):
    base = str(base)
    if len(base) != 12 or not base.isdigit():
        raise ValidationError("Основа EAN-13 должна состоять из 12 цифр.")
    total = sum((3 if index % 2 else 1) * int(number) for index, number in enumerate(base))
    return str((10 - total % 10) % 10)


def generate_iiko_barcode(article):
    article = str(article).strip().zfill(5)
    if len(article) != 5 or not article.isdigit():
        raise ValidationError("Артикул должен состоять из 5 цифр.")
    base = f"21{article}00001"
    return base + calculate_ean13_checksum(base)


def _user_name(user):
    return str(user)


def _product_snapshot(product):
    return {
        "product_name_snapshot": product.name,
        "product_category_snapshot": product.category,
        "product_article_snapshot": product.article,
        "product_barcode_snapshot": product.barcode,
    }


def _operation_snapshot(*, user, source=None, destination=None, product=None):
    data = {
        "user_name_snapshot": _user_name(user),
        "source_name_snapshot": source.name if source else "",
        "destination_name_snapshot": destination.name if destination else "",
    }
    if product:
        data.update(_product_snapshot(product))
    return data


def _can_manage_warehouse(user, warehouse):
    return (
        user.is_main_admin
        or warehouse.responsible_id == user.id
        or (warehouse.responsible_id is None and user.role == User.Role.RESPONSIBLE)
    )


@transaction.atomic
def create_product(*, warehouse, quantity, user, **data):
    name = data.get("name", "").strip()
    existing = Product.objects.filter(name__iexact=name).first()
    if existing:
        raise ValidationError(
            f"Номенклатура «{existing.name}» уже существует. Выберите её в поступлении."
        )
    data["name"] = name
    article = str(data.get("article") or generate_article()).strip().zfill(5)
    data["article"] = article
    data.setdefault("barcode", generate_iiko_barcode(article))
    product = Product.objects.create(**data)
    Stock.objects.create(warehouse=warehouse, product=product, quantity=quantity)
    OperationLog.objects.create(
        user=user,
        action="Первичное поступление",
        destination=warehouse,
        product=product,
        quantity=quantity,
        resulting_quantity=quantity,
        status="Принято на склад",
        **_operation_snapshot(
            user=user,
            destination=warehouse,
            product=product,
        ),
    )
    return product


def _log(
    transfer,
    user,
    action,
    resulting_quantity=None,
    admin_decision="",
    decision_at=None,
    old_value=None,
    new_value=None,
    metadata=None,
):
    OperationLog.objects.create(
        transfer=transfer,
        user=user,
        action=action,
        source=transfer.source,
        destination=transfer.destination,
        product=transfer.product,
        quantity=transfer.quantity,
        resulting_quantity=resulting_quantity,
        status=transfer.status,
        user_name_snapshot=_user_name(user),
        source_name_snapshot=transfer.source_name_snapshot,
        destination_name_snapshot=transfer.destination_name_snapshot,
        product_name_snapshot=transfer.product_name_snapshot,
        product_category_snapshot=transfer.product_category_snapshot,
        product_article_snapshot=transfer.product_article_snapshot,
        product_barcode_snapshot=transfer.product_barcode_snapshot,
        transfer_reason=transfer.reason,
        admin_decision=admin_decision,
        rejection_reason=transfer.sender_rejection_reason or transfer.rejection_reason,
        decision_user_name_snapshot=_user_name(user) if admin_decision else "",
        decision_at=decision_at,
        old_value=old_value,
        new_value=new_value,
        metadata=metadata,
    )


def create_operation_log(
    *,
    user,
    action,
    source=None,
    destination=None,
    product=None,
    quantity=None,
    resulting_quantity=None,
    status="",
    old_value=None,
    new_value=None,
    metadata=None,
):
    return OperationLog.objects.create(
        user=user,
        action=action,
        source=source,
        destination=destination,
        product=product,
        quantity=quantity,
        resulting_quantity=resulting_quantity,
        status=status,
        old_value=old_value,
        new_value=new_value,
        metadata=metadata,
        **_operation_snapshot(user=user, source=source, destination=destination, product=product),
    )


@transaction.atomic
def archive_product(product, user):
    if not user.is_main_admin:
        raise PermissionDenied("Архивировать товар может только администратор.")
    if not product.is_active:
        return product
    old_value = {"is_active": product.is_active, "archived_at": None}
    product.is_active = False
    product.archived_at = timezone.now()
    product.archived_by = user
    product.save(update_fields=["is_active", "archived_at", "archived_by"])
    create_operation_log(
        user=user,
        action="Номенклатура архивирована",
        product=product,
        status="Архив",
        old_value=old_value,
        new_value={"is_active": product.is_active, "archived_at": product.archived_at.isoformat()},
    )
    return product


@transaction.atomic
def restore_product(product, user):
    if not user.is_main_admin:
        raise PermissionDenied("Восстановить товар может только администратор.")
    if product.is_active:
        return product
    old_value = {"is_active": product.is_active, "archived_at": product.archived_at.isoformat() if product.archived_at else None}
    product.is_active = True
    product.archived_at = None
    product.archived_by = None
    product.save(update_fields=["is_active", "archived_at", "archived_by"])
    create_operation_log(
        user=user,
        action="Номенклатура восстановлена",
        product=product,
        status="Активна",
        old_value=old_value,
        new_value={"is_active": product.is_active, "archived_at": None},
    )
    return product


@transaction.atomic
def receive_stock(*, warehouse, product, quantity, user):
    if not _can_manage_warehouse(user, warehouse):
        raise PermissionDenied("Оформить поступление может ответственный этого склада.")
    if quantity < 1:
        raise ValidationError("Количество поступления должно быть больше нуля.")

    stock, _ = Stock.objects.select_for_update().get_or_create(
        warehouse=warehouse,
        product=product,
        defaults={"quantity": 0},
    )
    stock.quantity = F("quantity") + quantity
    stock.save(update_fields=["quantity", "updated_at"])
    stock.refresh_from_db()
    OperationLog.objects.create(
        user=user,
        action="Поступление товара",
        destination=warehouse,
        product=product,
        quantity=quantity,
        resulting_quantity=stock.quantity,
        status="Принято на склад",
        **_operation_snapshot(
            user=user,
            destination=warehouse,
            product=product,
        ),
    )
    return stock


@transaction.atomic
def create_transfer(*, source, destination, product, quantity, user, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Укажите причину перемещения.")
    if not _can_manage_warehouse(user, destination):
        raise PermissionDenied("Создавать заявку может ответственный склада-получателя.")
    if source == destination:
        raise ValidationError("Склады должны отличаться.")
    stock = Stock.objects.filter(warehouse=source, product=product).first()
    if not stock or stock.quantity < quantity:
        raise ValidationError("Недостаточно товара на складе-отправителе.")
    transfer = Transfer.objects.create(
        source=source,
        destination=destination,
        product=product,
        quantity=quantity,
        reason=reason,
        created_by=user,
        status=Transfer.Status.WAITING_SENDER_APPROVAL,
        source_name_snapshot=source.name,
        destination_name_snapshot=destination.name,
        created_by_name_snapshot=_user_name(user),
        **_product_snapshot(product),
    )
    _log(transfer, user, "Создано перемещение")
    return transfer


@transaction.atomic
def sender_approve_transfer(transfer_id, user):
    transfer = Transfer.objects.select_for_update().select_related("source").get(pk=transfer_id)
    if transfer.status != Transfer.Status.WAITING_SENDER_APPROVAL:
        raise ValidationError("Перемещение не ожидает согласия отправителя.")
    if not _can_manage_warehouse(user, transfer.source):
        raise PermissionDenied("Подтвердить согласие может ответственный склада-отправителя.")
    transfer.status = Transfer.Status.WAITING_ADMIN_APPROVAL
    transfer.sender_approved_at = timezone.now()
    transfer.sender_approved_by = user
    transfer.sender_approved_by_name_snapshot = _user_name(user)
    transfer.last_reminded_at = None
    transfer.save(update_fields=[
        "status",
        "sender_approved_at",
        "sender_approved_by",
        "sender_approved_by_name_snapshot",
        "last_reminded_at",
    ])
    _log(transfer, user, "Отправитель согласовал заявку", admin_decision="Отправитель согласовал", decision_at=transfer.sender_approved_at)
    return transfer


@transaction.atomic
def sender_reject_transfer(transfer_id, user, rejection_reason=None):
    rejection_reason = (rejection_reason or "").strip()
    if not rejection_reason:
        raise ValidationError("Укажите причину отказа отправителя.")
    transfer = Transfer.objects.select_for_update().select_related("source").get(pk=transfer_id)
    if transfer.status != Transfer.Status.WAITING_SENDER_APPROVAL:
        raise ValidationError("Перемещение не ожидает согласия отправителя.")
    if not _can_manage_warehouse(user, transfer.source):
        raise PermissionDenied("Отказать может ответственный склада-отправителя.")
    transfer.status = Transfer.Status.SENDER_REJECTED
    transfer.sender_rejected_at = timezone.now()
    transfer.sender_rejected_by = user
    transfer.sender_rejected_by_name_snapshot = _user_name(user)
    transfer.sender_rejection_reason = rejection_reason
    transfer.last_reminded_at = None
    transfer.save(update_fields=[
        "status",
        "sender_rejected_at",
        "sender_rejected_by",
        "sender_rejected_by_name_snapshot",
        "sender_rejection_reason",
        "last_reminded_at",
    ])
    _log(transfer, user, "Отправитель отказал в заявке", admin_decision="Отправитель отказал", decision_at=transfer.sender_rejected_at)
    return transfer


@transaction.atomic
def approve_transfer(transfer_id, user):
    transfer = Transfer.objects.select_for_update().select_related("source").get(pk=transfer_id)
    if transfer.status != Transfer.Status.WAITING_ADMIN_APPROVAL:
        raise ValidationError("Перемещение не ожидает согласования админа.")
    if not user.is_main_admin:
        raise PermissionDenied("Согласовать перемещение может только администратор.")
    transfer.status = Transfer.Status.APPROVED
    transfer.approved_at = timezone.now()
    transfer.approved_by = user
    transfer.approved_by_name_snapshot = _user_name(user)
    transfer.last_reminded_at = None
    transfer.save(update_fields=["status", "approved_at", "approved_by", "approved_by_name_snapshot", "last_reminded_at"])
    _log(transfer, user, "Перемещение согласовано", admin_decision="Согласовано", decision_at=transfer.approved_at)
    return transfer


@transaction.atomic
def reject_transfer(transfer_id, user, rejection_reason=None):
    rejection_reason = (rejection_reason or "").strip()
    if not rejection_reason:
        raise ValidationError("Укажите причину отказа.")
    transfer = Transfer.objects.select_for_update().select_related("source").get(pk=transfer_id)
    if transfer.status != Transfer.Status.WAITING_ADMIN_APPROVAL:
        raise ValidationError("Перемещение не ожидает согласования админа.")
    if not user.is_main_admin:
        raise PermissionDenied("Отклонить перемещение может только администратор.")
    transfer.status = Transfer.Status.ADMIN_REJECTED
    transfer.rejected_at = timezone.now()
    transfer.rejected_by = user
    transfer.rejected_by_name_snapshot = _user_name(user)
    transfer.rejection_reason = rejection_reason
    transfer.last_reminded_at = None
    transfer.save(update_fields=[
        "status",
        "rejected_at",
        "rejected_by",
        "rejected_by_name_snapshot",
        "rejection_reason",
        "last_reminded_at",
    ])
    _log(transfer, user, "Перемещение отклонено", admin_decision="Отклонено", decision_at=transfer.rejected_at)
    return transfer


@transaction.atomic
def ship_transfer(transfer_id, user, scanned_barcode=None):
    transfer = Transfer.objects.select_for_update().select_related("source").get(pk=transfer_id)
    if transfer.status != Transfer.Status.APPROVED:
        raise ValidationError("Перемещение не ожидает отправки.")
    if not _can_manage_warehouse(user, transfer.source):
        raise PermissionDenied("Подтвердить отправку может ответственный склада-отправителя.")
    stock = Stock.objects.select_for_update().filter(
        warehouse=transfer.source, product=transfer.product
    ).first()
    if not stock or stock.quantity < transfer.quantity:
        raise ValidationError("Недостаточно остатка для отправки.")
    stock.quantity = F("quantity") - transfer.quantity
    stock.save(update_fields=["quantity", "updated_at"])
    stock.refresh_from_db()
    transfer.status = Transfer.Status.IN_TRANSIT
    transfer.shipped_at = timezone.now()
    transfer.last_reminded_at = None
    transfer.save(update_fields=["status", "shipped_at", "last_reminded_at"])
    _log(transfer, user, "Товар отгружен", resulting_quantity=stock.quantity, metadata={"barcode": scanned_barcode, "source": "telegram_scan"} if scanned_barcode else None)
    return transfer


@transaction.atomic
def receive_transfer(transfer_id, user, scanned_barcode=None):
    transfer = Transfer.objects.select_for_update().select_related("destination").get(pk=transfer_id)
    if transfer.status not in {Transfer.Status.IN_TRANSIT, Transfer.Status.SHIPPED}:
        raise ValidationError("Перемещение не находится в пути.")
    if not _can_manage_warehouse(user, transfer.destination):
        raise PermissionDenied("Подтвердить получение может ответственный склада-получателя.")
    stock, _ = Stock.objects.select_for_update().get_or_create(
        warehouse=transfer.destination, product=transfer.product, defaults={"quantity": 0}
    )
    stock.quantity = F("quantity") + transfer.quantity
    stock.save(update_fields=["quantity", "updated_at"])
    stock.refresh_from_db()
    now = timezone.now()
    transfer.status = Transfer.Status.COMPLETED
    transfer.received_at = now
    transfer.completed_at = now
    transfer.last_reminded_at = None
    transfer.save(update_fields=["status", "received_at", "completed_at", "last_reminded_at"])
    _log(
        transfer,
        user,
        "Товар получен, перемещение завершено",
        resulting_quantity=stock.quantity,
    metadata={"barcode": scanned_barcode, "source": "telegram_scan"} if scanned_barcode else None,
    )
    return transfer
