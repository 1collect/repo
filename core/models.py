import secrets
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Админ"
        RESPONSIBLE = "responsible", "Пользователь"

    role = models.CharField("роль", max_length=20, choices=Role.choices, default=Role.RESPONSIBLE)
    telegram_id = models.BigIntegerField("Telegram ID", unique=True, null=True, blank=True)
    telegram_link_token = models.CharField(max_length=64, unique=True, blank=True)
    telegram_link_expires_at = models.DateTimeField("срок действия Telegram-ссылки", null=True, blank=True)
    telegram_link_used_at = models.DateTimeField("Telegram-ссылка использована", null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.telegram_link_token:
            self.refresh_telegram_link()
        super().save(*args, **kwargs)

    def unlink_telegram(self):
        self.telegram_id = None
        self.refresh_telegram_link()
        self.save(update_fields=[
            "telegram_id",
            "telegram_link_token",
            "telegram_link_expires_at",
            "telegram_link_used_at",
        ])

    def refresh_telegram_link(self, hours=24):
        self.telegram_link_token = secrets.token_urlsafe(24)
        self.telegram_link_expires_at = timezone.now() + timezone.timedelta(hours=hours)
        self.telegram_link_used_at = None

    @property
    def telegram_link_is_available(self):
        return (
            bool(self.telegram_link_token)
            and self.telegram_link_used_at is None
            and self.telegram_link_expires_at is not None
            and self.telegram_link_expires_at > timezone.now()
        )

    @property
    def is_main_admin(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    def __str__(self):
        return self.get_full_name() or self.username


class Warehouse(models.Model):
    name = models.CharField("название", max_length=200, unique=True)
    responsible = models.ForeignKey(
        User,
        verbose_name="ответственный",
        related_name="responsible_warehouses",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    is_active = models.BooleanField("активен", default=True)
    created_at = models.DateTimeField("создан", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "склад"
        verbose_name_plural = "склады"

    def clean(self):
        return super().clean()

    def __str__(self):
        return self.name


class Product(models.Model):
    article_validator = RegexValidator(r"^\d{5}$", "Артикул должен состоять из 5 цифр.")

    name = models.CharField("наименование", max_length=255)
    category = models.CharField("категория", max_length=150, blank=True)
    article = models.CharField(
        "артикул", max_length=5, unique=True, validators=[article_validator]
    )
    barcode = models.CharField("штрихкод", max_length=64, unique=True)
    description = models.TextField("описание", blank=True)
    created_at = models.DateTimeField("создан", auto_now_add=True)
    imported = models.BooleanField("импортирован", default=False)
    is_active = models.BooleanField("активен", default=True)
    archived_at = models.DateTimeField("архивирован", null=True, blank=True)
    archived_by = models.ForeignKey(
        User,
        verbose_name="архивировал",
        related_name="archived_products",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "номенклатура"
        verbose_name_plural = "номенклатура"

    def __str__(self):
        return f"{self.name} ({self.article})"


class Stock(models.Model):
    warehouse = models.ForeignKey(Warehouse, related_name="stocks", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, related_name="stocks", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField("количество", default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "product"], name="unique_warehouse_product")
        ]
        verbose_name = "остаток"
        verbose_name_plural = "остатки"

    def __str__(self):
        return f"{self.product} — {self.warehouse}: {self.quantity}"


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    uploaded_by = models.ForeignKey(
        User,
        related_name="import_batches",
        on_delete=models.PROTECT,
    )
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="imports/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.FAILED)
    total_rows = models.PositiveIntegerField(default=0)
    applied_rows = models.PositiveIntegerField(default=0)
    error_rows = models.PositiveIntegerField(default=0)
    errors_json = models.JSONField(null=True, blank=True)
    errors_text = models.TextField(blank=True)
    result_json = models.JSONField(null=True, blank=True)
    preview_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Excel import"
        verbose_name_plural = "Excel imports"

    def __str__(self):
        return f"{self.original_filename} ({self.status})"


class ProductHistory(models.Model):
    product = models.ForeignKey(Product, related_name="history_records", on_delete=models.CASCADE)
    changed_by = models.ForeignKey(User, related_name="+", on_delete=models.PROTECT)
    import_batch = models.ForeignKey(
        ImportBatch,
        related_name="product_history",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    old_value = models.JSONField()
    new_value = models.JSONField()
    changed_fields = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Product history"
        verbose_name_plural = "Product history"

    def __str__(self):
        return f"{self.product_id}: {self.created_at:%d.%m.%Y %H:%M}"


class StockHistory(models.Model):
    stock = models.ForeignKey(Stock, related_name="history_records", on_delete=models.CASCADE)
    warehouse = models.ForeignKey(Warehouse, related_name="+", on_delete=models.PROTECT)
    product = models.ForeignKey(Product, related_name="+", on_delete=models.PROTECT)
    changed_by = models.ForeignKey(User, related_name="+", on_delete=models.PROTECT)
    import_batch = models.ForeignKey(
        ImportBatch,
        related_name="stock_history",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    old_quantity = models.PositiveIntegerField()
    change_quantity = models.IntegerField()
    new_quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Stock history"
        verbose_name_plural = "Stock history"

    def __str__(self):
        return f"{self.product_id}/{self.warehouse_id}: {self.old_quantity} -> {self.new_quantity}"


class Transfer(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Создано"
        WAITING_SENDER_APPROVAL = "waiting_sender_approval", "Ожидает согласия отправителя"
        SENDER_REJECTED = "sender_rejected", "Отправитель отказал"
        WAITING_ADMIN_APPROVAL = "waiting_admin_approval", "Ожидает согласования админа"
        ADMIN_REJECTED = "admin_rejected", "Админ отклонил"
        APPROVED = "approved", "Согласовано"
        SHIPPED = "shipped", "Отгружено"
        IN_TRANSIT = "in_transit", "В пути"
        RECEIVED = "received", "Получено"
        COMPLETED = "completed", "Завершено"
        CANCELLED = "cancelled", "Отменено"

    source = models.ForeignKey(
        Warehouse, verbose_name="откуда", related_name="outgoing_transfers", on_delete=models.PROTECT
    )
    destination = models.ForeignKey(
        Warehouse, verbose_name="куда", related_name="incoming_transfers", on_delete=models.PROTECT
    )
    product = models.ForeignKey(Product, verbose_name="товар", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField("количество", validators=[MinValueValidator(1)])
    reason = models.TextField("причина перемещения", blank=True)
    status = models.CharField(
        "статус", max_length=30, choices=Status.choices, default=Status.CREATED, db_index=True
    )
    created_by = models.ForeignKey(
        User, verbose_name="создал", related_name="created_transfers", on_delete=models.PROTECT
    )
    approved_by = models.ForeignKey(
        User,
        verbose_name="согласовал",
        related_name="approved_transfers",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    sender_approved_by = models.ForeignKey(
        User,
        verbose_name="отправитель согласовал",
        related_name="sender_approved_transfers",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    sender_rejected_by = models.ForeignKey(
        User,
        verbose_name="отправитель отказал",
        related_name="sender_rejected_transfers",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    rejected_by = models.ForeignKey(
        User,
        verbose_name="отклонил",
        related_name="rejected_transfers",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("создано", auto_now_add=True)
    sender_approved_at = models.DateTimeField("отправитель согласовал", null=True, blank=True)
    sender_rejected_at = models.DateTimeField("отправитель отказал", null=True, blank=True)
    approved_at = models.DateTimeField("согласовано", null=True, blank=True)
    rejected_at = models.DateTimeField("отклонено", null=True, blank=True)
    shipped_at = models.DateTimeField("отгружено", null=True, blank=True)
    received_at = models.DateTimeField("получено", null=True, blank=True)
    completed_at = models.DateTimeField("завершено", null=True, blank=True)
    sender_rejection_reason = models.TextField("причина отказа отправителя", blank=True)
    rejection_reason = models.TextField("причина отказа", blank=True)
    last_reminded_at = models.DateTimeField("последнее напоминание", null=True, blank=True)
    source_name_snapshot = models.CharField("название склада-отправителя", max_length=200, blank=True)
    destination_name_snapshot = models.CharField("название склада-получателя", max_length=200, blank=True)
    product_name_snapshot = models.CharField("наименование товара", max_length=255, blank=True)
    product_category_snapshot = models.CharField("категория товара", max_length=150, blank=True)
    product_article_snapshot = models.CharField("артикул товара", max_length=5, blank=True)
    product_barcode_snapshot = models.CharField("штрихкод товара", max_length=64, blank=True)
    created_by_name_snapshot = models.CharField("имя создателя", max_length=300, blank=True)
    sender_approved_by_name_snapshot = models.CharField("имя отправителя, согласовавшего заявку", max_length=300, blank=True)
    sender_rejected_by_name_snapshot = models.CharField("имя отправителя, отказавшего в заявке", max_length=300, blank=True)
    approved_by_name_snapshot = models.CharField("имя согласовавшего", max_length=300, blank=True)
    rejected_by_name_snapshot = models.CharField("имя отклонившего", max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "перемещение"
        verbose_name_plural = "перемещения"

    def clean(self):
        if self.source_id and self.source_id == self.destination_id:
            raise ValidationError({"destination": "Склады отправителя и получателя должны отличаться."})

    def __str__(self):
        return f"#{self.pk}: {self.source} → {self.destination}"


class OperationLog(models.Model):
    import_batch = models.ForeignKey(
        ImportBatch,
        related_name="operation_logs",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    transfer = models.ForeignKey(
        Transfer, related_name="operations", on_delete=models.CASCADE, null=True, blank=True
    )
    user = models.ForeignKey(User, verbose_name="пользователь", on_delete=models.PROTECT)
    action = models.CharField("действие", max_length=100)
    source = models.ForeignKey(
        Warehouse, related_name="+", on_delete=models.PROTECT, null=True, blank=True
    )
    destination = models.ForeignKey(
        Warehouse, related_name="+", on_delete=models.PROTECT, null=True, blank=True
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    resulting_quantity = models.PositiveIntegerField(
        "остаток после операции", null=True, blank=True
    )
    status = models.CharField(max_length=30, blank=True)
    user_name_snapshot = models.CharField("пользователь", max_length=300, blank=True)
    source_name_snapshot = models.CharField("склад-отправитель", max_length=200, blank=True)
    destination_name_snapshot = models.CharField("склад-получатель", max_length=200, blank=True)
    product_name_snapshot = models.CharField("наименование товара", max_length=255, blank=True)
    product_category_snapshot = models.CharField("категория товара", max_length=150, blank=True)
    product_article_snapshot = models.CharField("артикул товара", max_length=5, blank=True)
    product_barcode_snapshot = models.CharField("штрихкод товара", max_length=64, blank=True)
    transfer_reason = models.TextField("причина перемещения", blank=True)
    admin_decision = models.CharField("решение администратора", max_length=100, blank=True)
    rejection_reason = models.TextField("причина отказа", blank=True)
    decision_user_name_snapshot = models.CharField("пользователь, принявший решение", max_length=300, blank=True)
    decision_at = models.DateTimeField("дата решения", null=True, blank=True)
    old_value = models.JSONField("старое значение", null=True, blank=True)
    new_value = models.JSONField("новое значение", null=True, blank=True)
    metadata = models.JSONField("дополнительные данные", null=True, blank=True)
    created_at = models.DateTimeField("дата", auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "операция"
        verbose_name_plural = "история операций"

    def __str__(self):
        return f"{self.created_at:%d.%m.%Y %H:%M} — {self.action}"

