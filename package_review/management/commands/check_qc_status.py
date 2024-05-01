from django.conf import settings
from django.core.management.base import BaseCommand

from package_review.clients import AWSClient


class Command(BaseCommand):
    help = "Sends a messaage if QC is complete"

    def handle(self, *args, **options):
        if not settings.BASE_STORAGE_DIR.is_dir():
            self.stdout.write(self.style.ERROR(f'Root directory {str(settings.BASE_STORAGE_DIR)} for files waiting to be QCed does not exist.'))
            exit()
        sns_client = AWSClient('sns', settings.AWS['role_arn'])

        if not any(settings.BASE_STORAGE_DIR.iterdir()):
            sns_client.deliver_message(
                settings.AWS['sns_topic'],
                None,
                'No packages left to QC',
                'COMPLETE')

        self.stdout.write(self.style.SUCCESS("Status check complete"))
