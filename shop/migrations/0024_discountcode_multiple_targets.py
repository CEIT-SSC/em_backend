import django.db.models.deletion
from django.db import migrations, models


def copy_discount_targets(apps, schema_editor):
    DiscountCode = apps.get_model('shop', 'DiscountCode')
    DiscountTarget = apps.get_model('shop', 'DiscountTarget')

    DiscountTarget.objects.bulk_create([
        DiscountTarget(
            discount_code_id=discount.pk,
            content_type_id=discount.content_type_id,
            object_id=discount.object_id,
        )
        for discount in DiscountCode.objects.exclude(content_type_id=None).exclude(object_id=None)
    ])


def restore_single_discount_target(apps, schema_editor):
    DiscountCode = apps.get_model('shop', 'DiscountCode')
    DiscountTarget = apps.get_model('shop', 'DiscountTarget')

    for discount in DiscountCode.objects.all():
        target = DiscountTarget.objects.filter(discount_code_id=discount.pk).order_by('pk').first()
        if target:
            discount.content_type_id = target.content_type_id
            discount.object_id = target.object_id
            discount.save(update_fields=['content_type', 'object_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('shop', '0023_pack_bypass_item_time_limits'),
    ]

    operations = [
        migrations.CreateModel(
            name='DiscountTarget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('object_id', models.PositiveIntegerField(verbose_name='Discount target object id')),
                ('content_type', models.ForeignKey(
                    limit_choices_to=models.Q(
                        models.Q(('app_label', 'events'), ('model', 'presentation')),
                        models.Q(('app_label', 'events'), ('model', 'solocompetition')),
                        models.Q(('app_label', 'events'), ('model', 'competitionteam')),
                        models.Q(('app_label', 'shop'), ('model', 'product')),
                        models.Q(('app_label', 'shop'), ('model', 'pack')),
                        _connector='OR',
                    ),
                    on_delete=django.db.models.deletion.CASCADE,
                    to='contenttypes.contenttype',
                    verbose_name='Discount target type',
                )),
                ('discount_code', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='targets',
                    to='shop.discountcode',
                )),
            ],
            options={
                'verbose_name': 'Discount Target',
                'verbose_name_plural': 'Discount Targets',
                'indexes': [models.Index(fields=['content_type', 'object_id'], name='shop_dt_content_object_idx')],
                'constraints': [models.UniqueConstraint(
                    fields=('discount_code', 'content_type', 'object_id'),
                    name='unique_discount_target',
                )],
            },
        ),
        migrations.RunPython(copy_discount_targets, restore_single_discount_target),
        migrations.RemoveIndex(
            model_name='discountcode',
            name='shop_discou_content_2e737d_idx',
        ),
        migrations.RemoveField(
            model_name='discountcode',
            name='content_type',
        ),
        migrations.RemoveField(
            model_name='discountcode',
            name='object_id',
        ),
    ]
