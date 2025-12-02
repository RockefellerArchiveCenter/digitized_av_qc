from django.conf import settings
from django.core.management.base import BaseCommand

from package_review.clients import AWSClient
from package_review.models import Package


class Command(BaseCommand):
    help = "Sends a messaage if QC is complete"

    def handle(self, *args, **options):
        to_qc = Package.objects.filter(process_status=Package.PENDING).count()

        if not to_qc:
            sns_client = AWSClient('sns', settings.AWS['role_arn'])
            sns_client.deliver_message(
                settings.AWS['sns_topic'],
                None,
                'No packages left to QC',
                'COMPLETE')

        self.stdout.write(self.style.SUCCESS("Status check complete"))
