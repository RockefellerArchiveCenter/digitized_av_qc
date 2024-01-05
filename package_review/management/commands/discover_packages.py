import logging
import subprocess
import traceback
from os import getenv

from django.conf import settings
from django.core.management.base import BaseCommand

from package_review.clients import ArchivesSpaceClient, AWSClient
from package_review.models import Package

logging.basicConfig(
    level=int(getenv('LOGGING_LEVEL', logging.INFO)),
    format='%(filename)s::%(funcName)s::%(lineno)s %(message)s')


class Command(BaseCommand):
    help = "Discovers new packages to be QCed."

    def _get_type(self, root_path):
        refid = root_path.stem
        if (root_path / f'{refid}.mp3').exists():
            return Package.AUDIO
        elif (root_path / f'{refid}.mp4').exists():
            return Package.VIDEO
        else:
            raise Exception(f'Unable to determine type of package {refid}')

    def _get_duration(self, filepaths):
        duration = 0.0
        for fp in filepaths:
            process = subprocess.Popen(
                ['ffprobe',
                 '-v',
                 'error',
                 '-show_entries',
                 'format=duration',
                 '-of',
                 'default=noprint_wrappers=1:nokey=1',
                 fp],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
            out, _ = process.communicate()
            duration += float(out.decode())
        return duration

    def _has_multiple_masters(self, master_files):
        return bool(len(list(master_files)) > 1)

    def handle(self, *args, **options):
        if not settings.BASE_STORAGE_DIR.is_dir():
            self.stdout.write(self.style.ERROR(f'Root directory {str(settings.BASE_STORAGE_DIR)} for files waiting to be QCed does not exist.'))
            exit()
        created_list = []
        ssm_client = AWSClient('ssm', settings.AWS['role_arn']).client

        configuration = {}
        param_details = ssm_client.get_parameters_by_path(
            Path=f"/{getenv('ENV')}/{getenv('APP_CONFIG_PATH')}",
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
            repository=configuration.get('AS_REPO'))
        for package_path in settings.BASE_STORAGE_DIR.iterdir():
            refid = package_path.stem
            if not Package.objects.filter(refid=refid, process_status=Package.PENDING).exists():
                try:
                    title, av_number, uri, resource_title, resource_uri = client.get_package_data(refid)
                    package_type = self._get_type(package_path)
                    possible_duplicate = Package.objects.filter(refid=refid, process_status=Package.APPROVED).exists()
                    access_suffix, master_suffix = ('*.mp3', '*.wav') if package_type == Package.AUDIO else ('*.mp4', '*.mkv')
                    Package.objects.create(
                        title=title,
                        av_number=av_number,
                        uri=uri,
                        resource_title=resource_title,
                        resource_uri=resource_uri,
                        duration_access=self._get_duration(package_path.glob(access_suffix)),
                        duration_master=self._get_duration(package_path.glob(master_suffix)),
                        multiple_masters=self._has_multiple_masters(package_path.glob(master_suffix)),
                        possible_duplicate=possible_duplicate,
                        refid=refid,
                        type=package_type,
                        process_status=Package.PENDING)
                    created_list.append(refid)
                except Exception as e:
                    logging.exception(e)
                    exception = "\n".join(traceback.format_exception(e))
                    sns_client = AWSClient('sns', settings.AWS['role_arn'])
                    sns_client.deliver_message(
                        settings.AWS['sns_topic'],
                        None,
                        f'Error discovering refid {refid}\n\n{exception}',
                        'FAILURE')
                    continue

        message = f'Packages created: {", ".join(created_list)}' if len(created_list) else 'No new packages to discover.'
        self.stdout.write(self.style.SUCCESS(message))
