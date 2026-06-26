from rest_framework import serializers
from .models import Product, Stock, Transfer, Warehouse


class WarehouseSerializer(serializers.ModelSerializer):
    responsible = serializers.StringRelatedField()

    class Meta:
        model = Warehouse
        fields = ["id", "name", "responsible", "is_active"]


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "category",
            "article",
            "barcode",
            "description",
            "created_at",
        ]


class StockSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    article = serializers.CharField(source="product.article", read_only=True)

    class Meta:
        model = Stock
        fields = [
            "id",
            "warehouse",
            "warehouse_name",
            "product",
            "product_name",
            "article",
            "quantity",
            "updated_at",
        ]


class TransferSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    source_name = serializers.SerializerMethodField()
    destination_name = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    product_article = serializers.CharField(source="product_article_snapshot", read_only=True)
    product_barcode = serializers.CharField(source="product_barcode_snapshot", read_only=True)
    created_by = serializers.SerializerMethodField()

    def get_source_name(self, obj):
        return obj.source_name_snapshot or obj.source.name

    def get_destination_name(self, obj):
        return obj.destination_name_snapshot or obj.destination.name

    def get_product_name(self, obj):
        return obj.product_name_snapshot or obj.product.name

    def get_created_by(self, obj):
        return obj.created_by_name_snapshot or str(obj.created_by)

    class Meta:
        model = Transfer
        fields = [
            "id",
            "source",
            "source_name",
            "destination",
            "destination_name",
            "product",
            "product_name",
            "product_article",
            "product_barcode",
            "quantity",
            "reason",
            "status",
            "status_display",
            "created_by",
            "created_at",
            "sender_approved_by_name_snapshot",
            "sender_approved_at",
            "sender_rejected_by_name_snapshot",
            "sender_rejected_at",
            "sender_rejection_reason",
            "approved_by_name_snapshot",
            "approved_at",
            "rejected_by_name_snapshot",
            "rejected_at",
            "rejection_reason",
            "shipped_at",
            "received_at",
        ]
        read_only_fields = [
            "status",
            "created_by",
            "created_at",
            "sender_approved_by_name_snapshot",
            "sender_approved_at",
            "sender_rejected_by_name_snapshot",
            "sender_rejected_at",
            "sender_rejection_reason",
            "approved_by_name_snapshot",
            "approved_at",
            "rejected_by_name_snapshot",
            "rejected_at",
            "rejection_reason",
            "shipped_at",
            "received_at",
        ]
