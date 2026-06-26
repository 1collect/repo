from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/label/", views.product_label, name="product_label"),
    path("products/<int:pk>/archive/", views.product_archive, name="product_archive"),
    path("products/<int:pk>/restore/", views.product_restore, name="product_restore"),
    path("management/", views.management, name="management"),
    path("management/users/new/", views.management_user_create, name="management_user_create"),
    path(
        "management/users/<int:pk>/",
        views.management_user_edit,
        name="management_user_edit",
    ),
    path(
        "management/users/<int:pk>/unlink-telegram/",
        views.management_user_unlink_telegram,
        name="management_user_unlink_telegram",
    ),
    path(
        "management/warehouses/new/",
        views.management_warehouse_create,
        name="management_warehouse_create",
    ),
    path(
        "management/warehouses/<int:pk>/",
        views.management_warehouse_edit,
        name="management_warehouse_edit",
    ),
    path("stocks/", views.stock_list, name="stock_list"),
    path("stocks/receipt/", views.stock_receipt, name="stock_receipt"),
    path("scanner/", views.barcode_scanner, name="barcode_scanner"),
    path("scanner/upload/", views.barcode_scanner_upload, name="barcode_scanner_upload"),
    path("transfers/", views.transfer_list, name="transfer_list"),
    path("transfers/new/", views.transfer_create, name="transfer_create"),
    path("transfers/<int:pk>/", views.transfer_detail, name="transfer_detail"),
    path("transfers/<int:pk>/<str:action>/", views.transfer_action, name="transfer_action"),
    path("operations/", views.operation_list, name="operation_list"),
    path("import/", views.excel_import, name="excel_import"),
    path("import/<int:pk>/", views.excel_import_preview, name="excel_import_preview"),
    path("import/<int:pk>/apply/", views.excel_import_apply, name="excel_import_apply"),
    path("import/<int:pk>/delete/", views.excel_import_delete, name="excel_import_delete"),
    path("import/cleanup/", views.excel_import_cleanup, name="excel_import_cleanup"),
]

