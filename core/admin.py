from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.conf import settings
from django.utils.html import format_html
from .models import ImportBatch, OperationLog, Product, ProductHistory, Stock, StockHistory, Transfer, User, Warehouse


@admin.register(User)
class AssetUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (
            "AssetChain",
            {"fields": ("role", "telegram_id", "telegram_link_token", "telegram_connection_link")},
        ),
    )
    list_display = ("username", "first_name", "last_name", "role", "telegram_id", "is_active")
    readonly_fields = ("telegram_link_token", "telegram_connection_link")

    @admin.display(description="Ссылка подключения Telegram")
    def telegram_connection_link(self, obj):
        if not obj or not obj.pk:
            return "Появится после сохранения пользователя."
        if not settings.TELEGRAM_BOT_USERNAME:
            return "Укажите TELEGRAM_BOT_USERNAME в .env"
        url = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={obj.telegram_link_token}"
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "responsible", "is_active")
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "article", "barcode", "category", "imported")
    search_fields = ("name", "article", "barcode")
    list_filter = ("category", "imported")


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "product", "quantity", "updated_at")
    list_filter = ("warehouse",)
    search_fields = ("product__name", "product__article", "product__barcode")


@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ("id", "source", "destination", "product", "quantity", "status", "created_at")
    list_filter = ("status", "source", "destination")
    readonly_fields = ("created_at", "approved_at", "shipped_at", "received_at")


@admin.register(OperationLog)
class OperationLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "product", "quantity", "status")
    list_filter = ("action", "status")
    readonly_fields = [field.name for field in OperationLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("created_at", "uploaded_by", "original_filename", "status", "total_rows", "applied_rows", "error_rows")
    list_filter = ("status", "created_at")
    search_fields = ("original_filename", "uploaded_by__username")
    readonly_fields = [field.name for field in ImportBatch._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ProductHistory)
class ProductHistoryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "product", "changed_by", "import_batch")
    list_filter = ("created_at",)
    readonly_fields = [field.name for field in ProductHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StockHistory)
class StockHistoryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "warehouse", "product", "old_quantity", "change_quantity", "new_quantity", "changed_by")
    list_filter = ("created_at", "warehouse")
    readonly_fields = [field.name for field in StockHistory._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
