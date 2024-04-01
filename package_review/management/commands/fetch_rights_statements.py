from django.conf import settings
from django.core.management.base import BaseCommand

from package_review.clients import AquilaClient
from package_review.models import RightsStatement


class Command(BaseCommand):
    help = "Fetches rights statements from Aquila"

    def handle(self, *args, **options):
        created_list = []
        client = AquilaClient(settings.AQUILA['baseurl'])
        rights_statements = client.available_rights_statements()
        for statement in rights_statements:
            if not RightsStatement.objects.filter(aquila_id=statement['id']).exists():
                RightsStatement.objects.create(
                    aquila_id=statement['id'],
                    title=statement['title'])
                created_list.append(statement['id'])

        self.stdout.write(
            self.style.SUCCESS(f'Rights statmements created: {", ".join(created_list)}' if len(created_list) else 'No new rights statements.')
        )
