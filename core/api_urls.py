from rest_framework.routers import DefaultRouter
from .api import ProductViewSet, StockViewSet, TransferViewSet, WarehouseViewSet

router = DefaultRouter()
router.register("warehouses", WarehouseViewSet, basename="api-warehouse")
router.register("products", ProductViewSet, basename="api-product")
router.register("stocks", StockViewSet, basename="api-stock")
router.register("transfers", TransferViewSet, basename="api-transfer")

urlpatterns = router.urls
