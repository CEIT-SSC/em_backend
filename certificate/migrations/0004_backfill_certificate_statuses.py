from django.db import migrations


def backfill_generated_status(apps, schema_editor):
    Certificate = apps.get_model('certificate', 'Certificate')
    CompetitionCertificate = apps.get_model(
        'certificate',
        'CompetitionCertificate',
    )

    for model in (Certificate, CompetitionCertificate):
        model.objects.exclude(file_en__isnull=True).exclude(file_en='').exclude(
            file_fa__isnull=True
        ).exclude(file_fa='').update(status='generated')


def reset_status_to_pending(apps, schema_editor):
    Certificate = apps.get_model('certificate', 'Certificate')
    CompetitionCertificate = apps.get_model(
        'certificate',
        'CompetitionCertificate',
    )
    Certificate.objects.update(status='pending')
    CompetitionCertificate.objects.update(status='pending')


class Migration(migrations.Migration):
    dependencies = [
        (
            'certificate',
            '0003_certificate_generation_error_certificate_status_and_more',
        ),
    ]

    operations = [
        migrations.RunPython(
            backfill_generated_status,
            reverse_code=reset_status_to_pending,
        ),
    ]
