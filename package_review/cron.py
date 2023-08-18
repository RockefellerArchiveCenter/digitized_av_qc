from os import environ

from django.conf import settings
from django_cron import CronJobBase, Schedule

from .clients import AquilaClient, ArchivesSpaceClient, AWSClient
from .models import Package, RightsStatement


class DiscoverPackages(CronJobBase):
    """Discovers new packages waiting to be reviewed."""
    RUN_EVERY_MINS = 1

    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = "package_review.discover_packages"

    def _get_type(self, root_path):
        refid = root_path.stem
        if (root_path / f'{refid}_a.mp3').exists():
            return Package.AUDIO
        elif (root_path / f'{refid}_a.mp4').exists():
            return Package.VIDEO
        else:
            raise Exception(f'Unable to determine type of package {refid}')

    def do(self):
        created_list = []
        aws_client = AWSClient(
            'ssm',
            settings.AWS['access_key_id'],
            settings.AWS['secret_access_key'],
            settings.AWS['region'],
            settings.AWS['role_arn']).client
        configuration = {}
        param_details = aws_client.get_parameters_by_path(
            Path=f"/{environ.get('ENV')}/{environ.get('APP_CONFIG_PATH')}",
            Recursive=False,
            WithDecryption=True)
        for param in param_details.get('Parameters', []):
            param_path_array = param.get('Name').split("/")
            section_name = param_path_array[-1]
            configuration[section_name] = param.get('Value')
        client = ArchivesSpaceClient(
            baseurl=configuration.get('AS_BASEURL'),
            username=configuration.get('AS_USERNAME'),
            password=configuration.get('AS_PASSWORD'),
            repository=configuration.get('repository'))
        for package_path in settings.BASE_STORAGE_DIR.iterdir():
            refid = package_path.stem
            if not Package.objects.filter(refid=refid, process_status__in=[Package.PENDING, Package.APPROVED]).exists():
                title, av_number = client.get_package_data(refid)
                Package.objects.create(
                    title=title,
                    av_number=av_number,
                    refid=refid,
                    type=self._get_type(package_path),
                    process_status=Package.PENDING)
                created_list.append(refid)

        if len(created_list):
            return f'Packages created: {", ".join(created_list)}'
        else:
            sns_client = AWSClient(
                'sns',
                settings.AWS['access_key_id'],
                settings.AWS['secret_access_key'],
                settings.AWS['region'],
                settings.AWS['role_arn'])
            sns_client.deliver_message(
                settings.AWS['sns_topic'],
                None,
                'No packages left to QC',
                'COMPLETE')
            return 'No new packages to discover.'


class FetchRightsStatements(CronJobBase):
    """Fetches rights statements from Aquila"""
    RUN_EVERY_MINS = 1

    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = "package_review.fetch_rights_statements"

    def do(self):
        created_list = []
        client = AquilaClient(settings.AQUILA['baseurl'])
        rights_statements = client.available_rights_statements()
        for statement in rights_statements:
            if not RightsStatement.objects.filter(aquila_id=statement['id']).exists():
                RightsStatement.objects.create(
                    aquila_id=statement['id'],
                    title=statement['title'])
                created_list.append(statement['id'])

        return f'Rights statmements created: {", ".join(created_list)}' if len(created_list) else 'No new rights statements.'
