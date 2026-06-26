import base64
import os
import tempfile
from io import BytesIO

import barcode
from barcode.writer import SVGWriter
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import Http404, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .barcode_scanner import decode_barcode_from_image
from .forms import (
    ExcelImportForm,
    ManagementUserForm,
    ProductForm,
    StockReceiptForm,
    TransferForm,
    TransferRejectForm,
    WarehouseManagementForm,
)
from .importers import ImportHasErrors, apply_iiko_import_batch, prepare_iiko_workbook
from .models import ImportBatch, OperationLog, Product, Stock, Transfer, User, Warehouse
from .notifications import (
    notify_telegram_account_deactivated,
    notify_telegram_unlink,
    notify_transfer,
    notify_transfer_action_confirmed,
    notify_warehouse_responsible_changed,
    notify_user_access_change,
)
from .permissions import main_admin_required
from .services import (
    approve_transfer,
    archive_product,
    create_product,
    create_operation_log,
    create_transfer,
    receive_transfer,
    receive_stock,
    reject_transfer,
    restore_product,
    sender_approve_transfer,
    sender_reject_transfer,
    ship_transfer,
)


@csrf_exempt
@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def visible_warehouses(user):
    if user.is_main_admin:
        return Warehouse.objects.all()
    shared_filter = Q(responsible__isnull=True) if user.role == User.Role.RESPONSIBLE else Q()
    return Warehouse.objects.filter(
        Q(responsible=user) | shared_filter
    ).distinct()


def visible_transfers(user):
    qs = Transfer.objects.select_related(
        "source", "destination", "product", "created_by"
    )
    if user.is_main_admin:
        return qs
    shared_filter = (
        Q(source__responsible__isnull=True) | Q(destination__responsible__isnull=True)
        if user.role == User.Role.RESPONSIBLE
        else Q()
    )
    return qs.filter(
        Q(source__responsible=user)
        | Q(destination__responsible=user)
        | shared_filter
        | Q(created_by=user)
    ).distinct()


def visible_operations(user):
    operations = OperationLog.objects.select_related(
        "user", "source", "destination", "product"
    )
    if user.is_main_admin:
        return operations
    transfers = visible_transfers(user)
    warehouses = visible_warehouses(user)
    return operations.filter(
        Q(transfer__in=transfers)
        | Q(source__in=warehouses)
        | Q(destination__in=warehouses)
        | Q(user=user, source__isnull=True, destination__isnull=True)
    ).distinct()


def paginate(request, queryset, per_page=50):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def latest_operations_by_product(user, limit=10):
    latest = []
    seen_products = set()
    operations = visible_operations(user).exclude(product_id__isnull=True).order_by("-created_at")[:200]
    for operation in operations:
        if operation.product_id in seen_products:
            continue
        seen_products.add(operation.product_id)
        latest.append(operation)
        if len(latest) == limit:
            break
    return latest


@login_required
def dashboard(request):
    transfers = visible_transfers(request.user)
    warehouses = visible_warehouses(request.user)
    stocks = Stock.objects.filter(warehouse__in=warehouses)
    context = {
        "warehouse_count": warehouses.count(),
        "product_count": Product.objects.count(),
        "transfer_count": transfers.count(),
        "in_transit_count": transfers.filter(status=Transfer.Status.IN_TRANSIT).count(),
        "stock_total": stocks.aggregate(total=Sum("quantity"))["total"] or 0,
        "latest_operations": latest_operations_by_product(request.user),
        "low_stocks": stocks.filter(quantity__lte=3).select_related("warehouse", "product")[:10],
        "receipt_form": StockReceiptForm(user=request.user),
        "transfer_form": TransferForm(user=request.user),
    }
    return render(request, "core/dashboard.html", context)


@login_required
def product_list(request):
    products = Product.objects.prefetch_related("stocks__warehouse")
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    state = request.GET.get("state", "active").strip() or "active"
    if state == "archived":
        products = products.filter(is_active=False)
    elif state != "all":
        products = products.filter(is_active=True)
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(article__icontains=query) | Q(barcode__icontains=query)
        )
    if category:
        products = products.filter(category=category)
    page_obj = paginate(request, products)
    category_source = Product.objects.exclude(category="")
    if state == "archived":
        category_source = category_source.filter(is_active=False)
    elif state != "all":
        category_source = category_source.filter(is_active=True)
    categories = category_source.values_list("category", flat=True).distinct().order_by("category")
    return render(
        request,
        "core/product_list.html",
        {
            "products": page_obj,
            "page_obj": page_obj,
            "query": query,
            "categories": categories,
            "selected_category": category,
            "selected_state": state,
            "product_form": ProductForm() if request.user.is_main_admin else None,
        },
    )


@login_required
@main_admin_required
def product_create(request):
    form = ProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        product = create_product(user=request.user, **form.cleaned_data)
        messages.success(
            request,
            f"Создано: {product.name}. Начальный остаток "
            f"{form.cleaned_data['quantity']} шт. добавлен на склад "
            f"«{form.cleaned_data['warehouse']}».",
        )
        return redirect("product_list")
    return render(request, "core/form.html", {"form": form, "title": "Новая номенклатура"})


@login_required
def product_label(request, pk):
    product = get_object_or_404(Product, pk=pk)
    try:
        quantity = max(1, min(int(request.GET.get("quantity", 1)), 9999))
    except (TypeError, ValueError):
        quantity = 1
    try:
        copies = max(1, min(int(request.GET.get("copies", 1)), 9999))
    except (TypeError, ValueError):
        copies = 1

    barcode_class = (
        barcode.get_barcode_class("ean13")
        if len(product.barcode) == 13 and product.barcode.isdigit()
        else barcode.get_barcode_class("code128")
    )
    barcode_value = product.barcode[:-1] if barcode_class.name == "EAN-13" else product.barcode
    stream = BytesIO()
    barcode_class(barcode_value, writer=SVGWriter()).write(
        stream,
        {
            "module_width": 0.25,
            "module_height": 12,
            "quiet_zone": 1.5,
            "write_text": False,
        },
    )
    barcode_svg = base64.b64encode(stream.getvalue()).decode("ascii")
    return render(
        request,
        "core/product_label.html",
        {
            "product": product,
            "quantity": quantity,
            "copies": copies,
            "label_copies": range(copies),
            "barcode_svg": barcode_svg,
        },
    )


@login_required
@main_admin_required
@require_POST
def product_archive(request, pk):
    product = get_object_or_404(Product, pk=pk)
    try:
        archive_product(product, request.user)
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, "; ".join(error.messages) if hasattr(error, "messages") else str(error))
    else:
        messages.success(request, f"Товар «{product.name}» отправлен в архив.")
    return redirect("product_list")


@login_required
@main_admin_required
@require_POST
def product_restore(request, pk):
    product = get_object_or_404(Product, pk=pk)
    try:
        restore_product(product, request.user)
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, "; ".join(error.messages) if hasattr(error, "messages") else str(error))
    else:
        messages.success(request, f"Товар «{product.name}» восстановлен.")
    return redirect("product_list")


@login_required
@main_admin_required
def management(request):
    users = User.objects.order_by("-is_active", "first_name", "last_name", "username")
    warehouses = Warehouse.objects.select_related("responsible")
    for item in users:
        item.edit_form = ManagementUserForm(instance=item, request_user=request.user)
        item.edit_modal_id = f"userEditModal{item.pk}"
    for warehouse in warehouses:
        warehouse.edit_form = WarehouseManagementForm(instance=warehouse)
        warehouse.edit_modal_id = f"warehouseEditModal{warehouse.pk}"
    for item in users:
        if not item.telegram_link_is_available and item.is_active:
            item.refresh_telegram_link()
            item.save(update_fields=["telegram_link_token", "telegram_link_expires_at", "telegram_link_used_at"])
    return render(
        request,
        "core/management.html",
        {
            "users": users,
            "warehouses": warehouses,
            "telegram_bot_username": settings.TELEGRAM_BOT_USERNAME,
            "user_create_form": ManagementUserForm(request_user=request.user),
            "warehouse_create_form": WarehouseManagementForm(),
        },
    )


@login_required
@main_admin_required
def management_user_create(request):
    form = ManagementUserForm(request.POST or None, request_user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            form.add_error(
                "username",
                "Пользователь с таким логином уже существует. Выберите другой логин.",
            )
        else:
            messages.success(request, f"Пользователь {user} создан.")
            return redirect("management")
    return render(
        request,
        "core/management_form.html",
        {"form": form, "title": "Новый пользователь", "back_url": "management"},
    )


@login_required
@main_admin_required
def management_user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    original_role = user.role
    original_is_active = user.is_active
    original_telegram_id = user.telegram_id
    form = ManagementUserForm(
        request.POST or None,
        instance=user,
        request_user=request.user,
    )
    if request.method == "POST" and form.is_valid():
        access_changes = []
        if form.cleaned_data["role"] != original_role:
            old_role = dict(User.Role.choices).get(original_role, original_role)
            new_role = dict(User.Role.choices).get(form.cleaned_data["role"], form.cleaned_data["role"])
            access_changes.append(f"Роль: {old_role} → {new_role}")
        if form.cleaned_data["is_active"] != original_is_active:
            access_changes.append(
                "Доступ к системе включён"
                if form.cleaned_data["is_active"]
                else "Доступ к системе отключён"
            )
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            form.add_error(
                "username",
                "Пользователь с таким логином уже существует. Выберите другой логин.",
            )
        else:
            if access_changes and user.telegram_id:
                notify_user_access_change(user, request.user, access_changes)
            if original_is_active and not user.is_active and original_telegram_id:
                notify_telegram_account_deactivated(original_telegram_id)
                user.unlink_telegram()
                create_operation_log(
                    user=request.user,
                    action="Telegram отвязан из-за деактивации пользователя",
                    status="Пользователь неактивен",
                    old_value={"telegram_id": original_telegram_id, "is_active": original_is_active},
                    new_value={"telegram_id": None, "is_active": user.is_active},
                    metadata={"affected_user": str(user), "affected_user_id": user.pk},
                )
            messages.success(request, f"Пользователь {user} обновлён.")
            return redirect("management")
    return render(
        request,
        "core/management_form.html",
        {"form": form, "title": f"Пользователь: {user}", "back_url": "management"},
    )


@login_required
@main_admin_required
@require_POST
def management_user_unlink_telegram(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.telegram_id is None:
        messages.info(request, f"Telegram пользователя {user} уже не подключён.")
    else:
        notified = notify_telegram_unlink(user, request.user)
        user.unlink_telegram()
        if notified:
            messages.success(
                request,
                f"Пользователь {user} уведомлён, Telegram отвязан. Старая ссылка аннулирована.",
            )
        else:
            messages.warning(
                request,
                f"Telegram пользователя {user} отвязан, но отправить уведомление не удалось.",
            )
    return redirect("management")


@login_required
@main_admin_required
def management_warehouse_create(request):
    form = WarehouseManagementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        warehouse = form.save()
        messages.success(request, f"Склад «{warehouse}» создан.")
        return redirect("management")
    return render(
        request,
        "core/management_form.html",
        {"form": form, "title": "Новый склад", "back_url": "management"},
    )


@login_required
@main_admin_required
def management_warehouse_edit(request, pk):
    warehouse = get_object_or_404(Warehouse, pk=pk)
    old_responsible = warehouse.responsible
    form = WarehouseManagementForm(request.POST or None, instance=warehouse)
    if request.method == "POST" and form.is_valid():
        warehouse = form.save()
        if getattr(old_responsible, "pk", None) != warehouse.responsible_id:
            notify_warehouse_responsible_changed(
                warehouse,
                old_responsible,
                warehouse.responsible,
                request.user,
            )
            create_operation_log(
                user=request.user,
                action="??????? ????????????? ??????",
                destination=warehouse,
                status="Ответственный изменён",
                old_value={"responsible": str(old_responsible) if old_responsible else "Все"},
                new_value={"responsible": str(warehouse.responsible) if warehouse.responsible else "Все"},
            )
        messages.success(request, f"Склад «{warehouse}» обновлён.")
        return redirect("management")
    return render(
        request,
        "core/management_form.html",
        {"form": form, "title": f"Склад: {warehouse}", "back_url": "management"},
    )


@login_required
def stock_list(request):
    warehouses = visible_warehouses(request.user)
    stocks = Stock.objects.filter(warehouse__in=warehouses, product__is_active=True).select_related("warehouse", "product")
    warehouse_id = request.GET.get("warehouse")
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    if warehouse_id:
        stocks = stocks.filter(warehouse_id=warehouse_id)
    if query:
        stocks = stocks.filter(
            Q(product__name__icontains=query)
            | Q(product__article__icontains=query)
            | Q(product__barcode__icontains=query)
        )
    if category:
        stocks = stocks.filter(product__category=category)
    page_obj = paginate(request, stocks.order_by("warehouse__name", "product__name"))
    return render(
        request,
        "core/stock_list.html",
        {
            "stocks": page_obj,
            "page_obj": page_obj,
            "warehouses": warehouses,
            "selected_warehouse": warehouse_id,
            "query": query,
            "categories": Product.objects.exclude(category="").values_list(
                "category", flat=True
            ).distinct().order_by("category"),
            "selected_category": category,
            "receipt_form": StockReceiptForm(user=request.user),
        },
    )


@login_required
def stock_receipt(request):
    form = StockReceiptForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            if data.get("new_product_name"):
                product = create_product(
                    user=request.user,
                    warehouse=data["warehouse"],
                    quantity=data["quantity"],
                    name=data["new_product_name"],
                    category=data["new_product_category"].strip(),
                    description=data.get("new_product_description", "").strip(),
                )
                stock = Stock.objects.get(warehouse=data["warehouse"], product=product)
                created_new = True
            else:
                stock = receive_stock(
                    user=request.user,
                    warehouse=data["warehouse"],
                    product=data["product"],
                    quantity=data["quantity"],
                )
                created_new = False
        except (ValidationError, PermissionDenied) as error:
            form.add_error(
                None,
                "; ".join(error.messages) if hasattr(error, "messages") else str(error),
            )
        else:
            if created_new:
                messages.success(
                    request,
                    f"Создана номенклатура {stock.product.name} и принято "
                    f"{data['quantity']} шт. на склад «{stock.warehouse}».",
                )
            else:
                messages.success(
                    request,
                    f"Поступление оформлено: {stock.product.name} — "
                    f"{data['quantity']} шт. на склад «{stock.warehouse}». "
                    f"Текущий остаток: {stock.quantity} шт.",
                )
            return redirect("stock_list")
    return render(
        request,
        "core/stock_receipt_form.html",
        {"form": form},
    )


@login_required
def barcode_scanner(request):
    return render(request, "core/barcode_scanner.html")


@login_required
def barcode_scanner_upload(request):
    if request.method != "POST":
        return redirect("barcode_scanner")
    uploaded = request.FILES.get("image")
    if not uploaded:
        messages.error(request, "Выберите изображение с этикеткой.")
        return redirect("barcode_scanner")
    suffix = os.path.splitext(uploaded.name)[1] or ".jpg"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            for chunk in uploaded.chunks():
                temp_file.write(chunk)
            temp_path = temp_file.name
        barcode_value = decode_barcode_from_image(temp_path)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
    if not barcode_value:
        messages.error(request, "Не удалось считать штрихкод. Попробуйте фото ближе и чётче или введите код вручную.")
        return redirect("barcode_scanner")
    messages.success(request, f"Штрихкод распознан: {barcode_value}")
    return redirect(f"/products/?q={barcode_value}")

@login_required
@require_POST
def barcode_scanner_detect(request):
    uploaded = request.FILES.get("image")
    if not uploaded:
        return JsonResponse({"barcode": None, "error": "image_required"}, status=400)
    suffix = os.path.splitext(uploaded.name)[1] or ".jpg"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            for chunk in uploaded.chunks():
                temp_file.write(chunk)
            temp_path = temp_file.name
        barcode_value = decode_barcode_from_image(temp_path)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
    return JsonResponse({"barcode": barcode_value})

@login_required
def transfer_list(request):
    transfers = visible_transfers(request.user)
    status = request.GET.get("status", "")
    warehouse_id = request.GET.get("warehouse", "")
    query = request.GET.get("q", "").strip()
    if status:
        transfers = transfers.filter(status=status)
    if warehouse_id:
        transfers = transfers.filter(Q(source_id=warehouse_id) | Q(destination_id=warehouse_id))
    if query:
        transfers = transfers.filter(
            Q(product_name_snapshot__icontains=query)
            | Q(product_article_snapshot__icontains=query)
            | Q(product_barcode_snapshot__icontains=query)
            | Q(source_name_snapshot__icontains=query)
            | Q(destination_name_snapshot__icontains=query)
        )
    page_obj = paginate(request, transfers.order_by("created_at"))
    return render(
        request,
        "core/transfer_list.html",
        {
            "transfers": page_obj,
            "page_obj": page_obj,
            "statuses": Transfer.Status.choices,
            "warehouses": visible_warehouses(request.user),
            "selected_status": status,
            "selected_warehouse": warehouse_id,
            "query": query,
            "transfer_form": TransferForm(user=request.user),
        },
    )


@login_required
def transfer_create(request):
    form = TransferForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            transfer = create_transfer(user=request.user, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as error:
            form.add_error(None, "; ".join(error.messages) if hasattr(error, "messages") else str(error))
        else:
            notify_transfer(transfer)
            messages.success(request, f"Перемещение №{transfer.pk} создано.")
            return redirect("transfer_detail", pk=transfer.pk)
    return render(request, "core/form.html", {"form": form, "title": "Новое перемещение"})


@login_required
def transfer_detail(request, pk):
    transfer = get_object_or_404(visible_transfers(request.user), pk=pk)
    return render(request, "core/transfer_detail.html", {"transfer": transfer, "reject_form": TransferRejectForm()})


@login_required
@require_POST
def transfer_action(request, pk, action):
    if not visible_transfers(request.user).filter(pk=pk).exists():
        raise Http404
    actions = {
        "sender_approve": sender_approve_transfer,
        "sender_reject": sender_reject_transfer,
        "approve": approve_transfer,
        "reject": reject_transfer,
        "ship": ship_transfer,
        "receive": receive_transfer,
    }
    service = actions.get(action)
    if not service:
        raise Http404
    try:
        if action in {"reject", "sender_reject"}:
            form = TransferRejectForm(request.POST)
            if not form.is_valid():
                for errors in form.errors.values():
                    for error in errors:
                        messages.error(request, error)
                return redirect("transfer_detail", pk=pk)
            transfer = service(pk, request.user, form.cleaned_data["rejection_reason"])
        else:
            transfer = service(pk, request.user)
        notify_transfer(transfer)
        if action in {"ship", "receive"}:
            notify_transfer_action_confirmed(request.user, transfer, action)
        messages.success(request, "Операция выполнена.")
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, "; ".join(error.messages) if hasattr(error, "messages") else str(error))
    return redirect("transfer_detail", pk=pk)


@login_required
def operation_list(request):
    operations = visible_operations(request.user)
    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    warehouse_id = request.GET.get("warehouse", "").strip()
    if query:
        operations = operations.filter(
            Q(product_name_snapshot__icontains=query)
            | Q(product_article_snapshot__icontains=query)
            | Q(action__icontains=query)
            | Q(user_name_snapshot__icontains=query)
        )
    if action:
        operations = operations.filter(action=action)
    if warehouse_id:
        operations = operations.filter(
            Q(source_id=warehouse_id) | Q(destination_id=warehouse_id)
        )
    page_obj = paginate(request, operations.order_by("-created_at"))
    return render(
        request,
        "core/operation_list.html",
        {
            "operations": page_obj,
            "page_obj": page_obj,
            "query": query,
            "actions": visible_operations(request.user).values_list(
                "action", flat=True
            ).distinct().order_by("action"),
            "selected_action": action,
            "warehouses": visible_warehouses(request.user),
            "selected_warehouse": warehouse_id,
        },
    )


@login_required
@main_admin_required
def excel_import(request):
    form = ExcelImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        result = prepare_iiko_workbook(form.cleaned_data["file"], request.user)
        batch = result["batch"]
        if result.get("success"):
            messages.success(request, "Файл загружен. Проверьте строки и нажмите «Применить».")
        else:
            messages.error(request, "В файле есть ошибки. База не изменена.")
        return redirect("excel_import_preview", pk=batch.pk)
    import_query = request.GET.get("q", "").strip()
    import_status = request.GET.get("status", "").strip()
    imports = ImportBatch.objects.select_related("uploaded_by").filter(is_deleted=False).order_by("-created_at")
    if import_query:
        imports = imports.filter(
            Q(original_filename__icontains=import_query)
            | Q(uploaded_by__username__icontains=import_query)
            | Q(uploaded_by__first_name__icontains=import_query)
            | Q(uploaded_by__last_name__icontains=import_query)
        )
    if import_status:
        imports = imports.filter(status=import_status)
    return render(
        request,
        "core/import.html",
        {
            "form": form,
            "imports": imports[:50],
            "import_query": import_query,
            "import_status": import_status,
            "import_statuses": ImportBatch.Status.choices,
        },
    )


@login_required
@main_admin_required
def excel_import_preview(request, pk):
    batch = get_object_or_404(ImportBatch.objects.select_related("uploaded_by"), pk=pk, is_deleted=False)
    rows = batch.preview_json or []
    state = request.GET.get("state", "").strip()
    query = request.GET.get("q", "").strip().lower()
    if state == "error":
        rows = [row for row in rows if row.get("status") == "error"]
    elif state == "ok":
        rows = [row for row in rows if row.get("status") != "error"]
    if query:
        def matches(row):
            values = row.get("values") or {}
            errors = row.get("errors") or []
            haystack = " ".join(
                [str(row.get("sheet", "")), str(row.get("row", "")), " ".join(errors)]
                + [f"{key} {value}" for key, value in values.items()]
            ).lower()
            return query in haystack
        rows = [row for row in rows if matches(row)]
    return render(request, "core/import_preview.html", {"batch": batch, "rows": rows, "state": state, "query": request.GET.get("q", "")})


@login_required
@main_admin_required
@require_POST
def excel_import_apply(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, is_deleted=False)
    try:
        totals = apply_iiko_import_batch(batch, request.user)
    except ImportHasErrors as error:
        messages.error(request, str(error))
        return redirect("excel_import_preview", pk=batch.pk)
    messages.success(request, f"Импорт применён: товаров {totals['products']}, складов {totals['warehouses']}, поступлений {totals['stocks']}.")
    return redirect("dashboard")


@login_required
@main_admin_required
@require_POST
def excel_import_delete(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, is_deleted=False)
    filename = batch.original_filename
    file_present = bool(batch.file)
    if batch.file:
        try:
            batch.file.delete(save=False)
        except FileNotFoundError:
            pass
    OperationLog.objects.create(
        import_batch=batch,
        user=request.user,
        action="Excel import deleted",
        status="Файл удалён",
        user_name_snapshot=str(request.user),
        old_value={"file": filename, "file_present": file_present, "status": batch.status},
        new_value={"deleted": True},
    )
    batch.file = None
    batch.is_deleted = True
    batch.save(update_fields=["file", "is_deleted"])
    messages.success(request, f"Импорт «{filename}» удалён.")
    return redirect("excel_import")

@login_required
@main_admin_required
@require_POST
def excel_import_cleanup(request):
    deleted = 0
    for batch in ImportBatch.objects.exclude(file=""):
        if not batch.file:
            continue
        try:
            batch.file.delete(save=False)
        except FileNotFoundError:
            pass
        batch.file = None
        batch.save(update_fields=["file"])
        deleted += 1
    OperationLog.objects.create(
        user=request.user,
        action="Очистка Excel-файлов",
        status="Файлы удалены",
        quantity=deleted,
        user_name_snapshot=str(request.user),
        old_value={"stored_files": deleted},
        new_value={"stored_files": 0},
    )
    messages.success(request, f"Удалено {deleted} файлов.")
    return redirect("excel_import")



