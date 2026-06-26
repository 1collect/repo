from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="importbatch",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("success", "Success"),
                    ("failed", "Failed"),
                ],
                default="failed",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="importbatch",
            name="is_deleted",
            field=models.BooleanField(default=False),
        ),
    ]