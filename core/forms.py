from django import forms
from django.db.models import Q
from .models import Product, Stock, Transfer, User, Warehouse


class ProductChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, product):
        category = product.category or "Без категории"
        return f"{category} — {product.name} ({product.article})"


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-check-input"
                if isinstance(field.widget, forms.CheckboxInput)
                else "form-select"
                if isinstance(field.widget, forms.Select)
                else "form-control"
            )


class ProductForm(BootstrapFormMixin, forms.ModelForm):
    warehouse = forms.ModelChoiceField(
        label="Склад",
        queryset=Warehouse.objects.none(),
        help_text="На какой склад поставить начальный остаток.",
    )
    quantity = forms.IntegerField(
        label="Начальное количество",
        min_value=1,
        max_value=1_000_000,
        help_text="Количество товара при создании номенклатуры.",
    )

    class Meta:
        model = Product
        fields = ["name", "category", "description"]
        labels = {
            "name": "Наименование",
            "category": "Категория",
            "description": "Описание",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = True
        self.fields["category"].widget.attrs["placeholder"] = "Например: Холодильники"
        self.fields["name"].widget.attrs["placeholder"] = "Например: Холодильник Vega X200"
        self.fields["description"].widget.attrs["rows"] = 3
        self.fields["warehouse"].queryset = Warehouse.objects.filter(is_active=True).order_by("name")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Product.objects.filter(name__iexact=name, is_active=True).exists():
            raise forms.ValidationError(
                "Такая номенклатура уже существует. Оформите поступление существующего товара."
            )
        return name


class ManagementUserForm(BootstrapFormMixin, forms.ModelForm):
    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput,
        required=False,
        help_text="При редактировании оставьте пустым, чтобы сохранить текущий пароль.",
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "is_active",
        ]
        labels = {
            "username": "Логин",
            "first_name": "Имя",
            "last_name": "Фамилия",
            "email": "Email",
            "role": "Роль",
            "is_active": "Активен",
        }

    def __init__(self, *args, request_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_user = request_user
        self.fields["role"].choices = [
            (User.Role.ADMIN, "\u0410\u0434\u043c\u0438\u043d"),
            (User.Role.RESPONSIBLE, "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c"),
        ]
        if not self.instance.pk:
            self.fields["password"].required = True
            self.fields["password"].help_text = "Минимум 8 символов."

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if password and len(password) < 8:
            raise forms.ValidationError("Пароль должен содержать не менее 8 символов.")
        return password

    def clean_is_active(self):
        is_active = self.cleaned_data["is_active"]
        if self.instance.pk == getattr(self.request_user, "pk", None) and not is_active:
            raise forms.ValidationError("Нельзя отключить собственную учетную запись.")
        return is_active

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user


class WarehouseManagementForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["name", "responsible", "is_active"]
        labels = {
            "name": "Название",
            "responsible": "Ответственный",
            "is_active": "Активен",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_users = User.objects.filter(is_active=True).order_by("first_name", "last_name", "username")
        responsible_users = active_users.filter(
            role__in=[User.Role.RESPONSIBLE, User.Role.ADMIN]
        )
        if self.instance.pk:
            if self.instance.responsible_id:
                responsible_users = User.objects.filter(
                    Q(pk=self.instance.responsible_id)
                    | Q(is_active=True, role__in=[User.Role.RESPONSIBLE, User.Role.ADMIN])
                )
        self.fields["responsible"].queryset = responsible_users.order_by(
            "first_name", "last_name", "username"
        )
        self.fields["responsible"].empty_label = "Все"
        self.fields["responsible"].required = False
        self.fields["responsible"].help_text = (
            "Выберите конкретного ответственного или «Все», чтобы склад был доступен всем ответственным пользователям."
        )


class StockReceiptForm(BootstrapFormMixin, forms.Form):
    warehouse = forms.ModelChoiceField(
        label="Склад поступления",
        queryset=Warehouse.objects.none(),
    )
    product = ProductChoiceField(
        label="Существующий товар",
        queryset=Product.objects.none(),
        required=False,
        help_text="Сначала найдите и выберите товар, чтобы не создавать дубликат.",
    )
    new_product_name = forms.CharField(
        label="Новая номенклатура",
        max_length=255,
        required=False,
        help_text="Только если нужного товара ещё нет в списке.",
    )
    new_product_category = forms.CharField(
        label="Категория нового товара",
        max_length=150,
        required=False,
    )
    new_product_description = forms.CharField(
        label="Описание нового товара",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    quantity = forms.IntegerField(
        label="Количество",
        min_value=1,
        max_value=1_000_000,
        help_text="Количество единиц, фактически принятых на склад.",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        warehouses = Warehouse.objects.filter(is_active=True)
        if user and not user.is_main_admin:
            warehouses = warehouses.filter(responsible=user)
            if user.role == User.Role.RESPONSIBLE:
                warehouses = warehouses | Warehouse.objects.filter(responsible__isnull=True, is_active=True)
        self.fields["warehouse"].queryset = warehouses.order_by("name")
        self.fields["product"].queryset = Product.objects.filter(is_active=True).order_by("category", "name", "article")
        if not user or not user.is_main_admin:
            self.fields.pop("new_product_name")
            self.fields.pop("new_product_category")
            self.fields.pop("new_product_description")

    def clean(self):
        data = super().clean()
        product = data.get("product")
        new_name = data.get("new_product_name", "").strip()

        if product and new_name:
            self.add_error(
                "new_product_name",
                "Выберите существующий товар или создайте новый — не оба варианта сразу.",
            )
        elif not product and not new_name:
            self.add_error("product", "Выберите существующий товар.")
            if self.user and self.user.is_main_admin:
                self.add_error(
                    "new_product_name",
                    "Либо укажите название новой номенклатуры.",
                )

        if new_name:
            existing = Product.objects.filter(name__iexact=new_name, is_active=True).first()
            if existing:
                self.add_error(
                    "new_product_name",
                    f"Товар «{existing.name}» уже существует. Выберите его в списке.",
                )
            if not data.get("new_product_category", "").strip():
                self.add_error(
                    "new_product_category",
                    "Для новой номенклатуры укажите категорию.",
                )
            data["new_product_name"] = new_name
        return data


class TransferForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Transfer
        fields = ["source", "destination", "product", "quantity", "reason"]
        labels = {
            "source": "Откуда",
            "destination": "Куда",
            "product": "Товар",
            "quantity": "Количество",
            "reason": "Причина перемещения",
        }
        widgets = {
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user and not user.is_main_admin:
            user_warehouse = (
                Warehouse.objects.filter(responsible=user, is_active=True).first()
                or (
                    Warehouse.objects.filter(responsible__isnull=True, is_active=True).first()
                    if user.role == User.Role.RESPONSIBLE
                    else None
                )
            )
            self.fields["source"].queryset = Warehouse.objects.filter(is_active=True).exclude(pk=getattr(user_warehouse, "pk", None))
            destinations = Warehouse.objects.filter(responsible=user, is_active=True)
            if user.role == User.Role.RESPONSIBLE:
                destinations = destinations | Warehouse.objects.filter(responsible__isnull=True, is_active=True)
            self.fields["destination"].queryset = destinations.distinct().order_by("name")
            self.fields["destination"].initial = user_warehouse
            self.fields["destination"].disabled = True
        else:
            self.fields["source"].queryset = Warehouse.objects.filter(is_active=True)
            self.fields["destination"].queryset = Warehouse.objects.filter(is_active=True)
        self.fields["product"].queryset = Product.objects.filter(is_active=True).order_by("category", "name", "article")
        source_id = self.data.get(self.add_prefix("source")) or self.initial.get("source")
        if source_id:
            self.fields["product"].queryset = Product.objects.filter(
                is_active=True,
                stocks__warehouse_id=source_id,
                stocks__quantity__gt=0,
            ).distinct().order_by("category", "name", "article")
        self.fields["reason"].required = True
        self.fields["reason"].widget.attrs["placeholder"] = "Например: новый сотрудник, замена оборудования, открытие рабочего места"

    def clean(self):
        data = super().clean()
        if self.user and not self.user.is_main_admin:
            user_warehouse = (
                Warehouse.objects.filter(responsible=self.user, is_active=True).first()
                or (
                    Warehouse.objects.filter(responsible__isnull=True, is_active=True).first()
                    if self.user.role == User.Role.RESPONSIBLE
                    else None
                )
            )
            if not user_warehouse:
                raise forms.ValidationError("Пользователь не привязан к активному складу.")
            data["destination"] = user_warehouse
        if data.get("source") == data.get("destination"):
            self.add_error("destination", "Выберите другой склад.")
        source = data.get("source")
        product = data.get("product")
        quantity = data.get("quantity")
        if source and product and quantity:
            stock = Stock.objects.filter(warehouse=source, product=product).first()
            if not stock or stock.quantity < quantity:
                self.add_error("quantity", "На складе-отправителе недостаточно товара.")
        reason = data.get("reason", "").strip()
        if not reason:
            self.add_error("reason", "Укажите причину перемещения.")
        data["reason"] = reason
        return data


class TransferRejectForm(BootstrapFormMixin, forms.Form):
    rejection_reason = forms.CharField(
        label="Причина отказа",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Например: требуется уточнение количества"}),
        max_length=2000,
    )


class ExcelImportForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField(
        label="Excel-файл (.xlsx)",
        help_text="Листы: Номенклатура, Склады, Остатки. Шаблон описан на странице импорта.",
    )

