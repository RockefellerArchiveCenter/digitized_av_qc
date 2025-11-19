import logging
import subprocess
import traceback
from os import getenv

from django.conf import settings
from django.core.management.base import BaseCommand

from package_review.clients import ArchivesSpaceClient, AWSClient
from package_review.helpers import get_config
from package_review.models import Package

logging.basicConfig(
    level=int(getenv('LOGGING_LEVEL', logging.INFO)),
    format='%(filename)s::%(funcName)s::%(lineno)s %(message)s')


class Command(BaseCommand):
    help = "Discovers new packages to be QCed."

    def add_arguments(self, parser):
        parser.add_argument("refid")

    def _get_type(self, refid, bucket_name, s3_client):
        if s3_client.key_exists(bucket_name, f"{refid}/{refid}.mp3"):
            return Package.AUDIO
        elif s3_client.key_exists(bucket_name, f"{refid}/{refid}.mp4"):
            return Package.VIDEO
        else:
            raise Exception(f'Unable to determine type of package {refid}')

    def _get_duration(self, file_urls):
        duration = 0.0
        for fp in file_urls:
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
            out, e = process.communicate()
            duration += float(out.decode())
        return duration

    def _has_multiple_masters(self, master_files):
        return bool(len(master_files) > 1)

    def handle(self, *args, **options):
        configuration = get_config(f"/{getenv('ENV')}/{getenv('APP_CONFIG_PATH')}")
        client = ArchivesSpaceClient(
            baseurl=configuration.get('AS_BASEURL'),
            username=configuration.get('AS_USERNAME'),
            password=configuration.get('AS_PASSWORD'),
            repository=configuration.get('AS_REPO'))
        s3_client = AWSClient('s3', settings.AWS['role_arn'])

        refid = options['refid']
        try:
            title, av_number, uri, resource_title, resource_uri, undated_object = client.get_package_data(refid)
            size = s3_client.calculate_package_size(refid)
            package_type = self._get_type(refid, settings.AWS['bucket'], s3_client)
            possible_duplicate = Package.objects.filter(refid=refid, process_status=Package.APPROVED).exists()
            access_suffix, master_suffix = ('.mp3', '.wav') if package_type == Package.AUDIO else ('.mp4', '.mkv')
            access_file_urls = s3_client.get_signed_urls(refid, settings.AWS['bucket'], access_suffix)
            master_file_urls = s3_client.get_signed_urls(refid, settings.AWS['bucket'], master_suffix)
            Package.objects.create(
                title=title,
                av_number=av_number,
                uri=uri,
                resource_title=resource_title,
                resource_uri=resource_uri,
                duration_access=self._get_duration(access_file_urls),
                duration_master=self._get_duration(master_file_urls),
                multiple_masters=self._has_multiple_masters(master_file_urls),
                possible_duplicate=possible_duplicate,
                refid=refid,
                size_bytes=size,
                type=package_type,
                undated_object=undated_object,
                process_status=Package.PENDING)
            message = f'Package created: {refid}'
            self.stdout.write(self.style.SUCCESS(message))
        except Exception as e:
            logging.exception(e)
            exception = "\n".join(traceback.format_exception(e))
            sns_client = AWSClient('sns', settings.AWS['role_arn'])
            sns_client.deliver_message(
                settings.AWS['sns_topic'],
                None,
                f'Error discovering refid {refid}',
                'FAILURE',
                traceback=exception)
            message = f'Error creating packages: {e}'
            self.stdout.write(self.style.ERROR(message))
