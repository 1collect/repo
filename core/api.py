from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .models import Product, Stock, Transfer, Warehouse
from .notifications import notify_transfer
from .serializers import (
    ProductSerializer,
    StockSerializer,
    TransferSerializer,
    WarehouseSerializer,
)
from .services import (
    approve_transfer,
    create_transfer,
    receive_transfer,
    reject_transfer,
    sender_approve_transfer,
    sender_reject_transfer,
    ship_transfer,
)
from .views import visible_transfers, visible_warehouses


def run_service(service, *args, **kwargs):
    try:
        return service(*args, **kwargs)
    except DjangoPermissionDenied as error:
        raise PermissionDenied(str(error)) from error
    except DjangoValidationError as error:
        raise ValidationError(error.messages) from error


class WarehouseViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = WarehouseSerializer

    def get_queryset(self):
        return visible_warehouses(self.request.user)


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProductSerializer

    def get_queryset(self):
        queryset = Product.objects.filter(is_active=True)
        query = self.request.query_params.get("q")
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(article__icontains=query)
                | Q(barcode__icontains=query)
            )
        return queryset


class StockViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StockSerializer

    def get_queryset(self):
        queryset = Stock.objects.filter(
            warehouse__in=visible_warehouses(self.request.user),
            product__is_active=True,
        ).select_related("warehouse", "product")
        warehouse = self.request.query_params.get("warehouse")
        if warehouse:
            queryset = queryset.filter(warehouse_id=warehouse)
        return queryset


class TransferViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = TransferSerializer

    def get_queryset(self):
        return visible_transfers(self.request.user)

    def perform_create(self, serializer):
        transfer = run_service(
            create_transfer,
            user=self.request.user,
            **serializer.validated_data,
        )
        serializer.instance = transfer
        notify_transfer(transfer)

    def _transition(self, service):
        transfer = run_service(service, self.get_object().pk, self.request.user)
        notify_transfer(transfer)
        return Response(self.get_serializer(transfer).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._transition(approve_transfer)

    @action(detail=True, methods=["post"])
    def sender_approve(self, request, pk=None):
        return self._transition(sender_approve_transfer)

    @action(detail=True, methods=["post"])
    def sender_reject(self, request, pk=None):
        transfer = run_service(
            sender_reject_transfer,
            self.get_object().pk,
            self.request.user,
            request.data.get("rejection_reason"),
        )
        notify_transfer(transfer)
        return Response(self.get_serializer(transfer).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        transfer = run_service(
            reject_transfer,
            self.get_object().pk,
            self.request.user,
            request.data.get("rejection_reason"),
        )
        notify_transfer(transfer)
        return Response(self.get_serializer(transfer).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def ship(self, request, pk=None):
        return self._transition(ship_transfer)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        return self._transition(receive_transfer)
