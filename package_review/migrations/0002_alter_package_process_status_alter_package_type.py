# Generated by Django 4.2.3 on 2023-08-02 19:21

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('package_review', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='package',
            name='process_status',
            field=models.IntegerField(choices=[(0, 'Pending'), (9, 'Approved'), (5, 'Rejected')]),
        ),
        migrations.AlterField(
            model_name='package',
            name='type',
            field=models.IntegerField(choices=[(1, 'Audio'), (2, 'Video')]),
        ),
    ]
