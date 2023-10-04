import json
import random
import shutil
from pathlib import Path
from unittest.mock import patch

import boto3
from django.conf import settings
from django.shortcuts import reverse
from django.test import TestCase
from moto import mock_sns, mock_sqs, mock_ssm, mock_sts
from moto.core import DEFAULT_ACCOUNT_ID

from .clients import ArchivesSpaceClient, AWSClient
from .management.commands import (check_qc_status, discover_packages,
                                  fetch_rights_statements)
from .models import Package, RightsStatement

FIXTURE_DIR = "fixtures"
RIGHTS_DATA = [("1", "foo"), ("2", "bar")]
PACKAGE_DATA = [("foo", "av 123", 123.45, 123.45, False, "9ba10e5461d401517b0e1a53d514ec87", Package.AUDIO),
                ("bar", "av 321", 543.21, 543.21, True, "f7d3dd6dc9c4732fa17dbd88fbe652b6", Package.VIDEO)]


def create_rights_statements():
    for aquila_id, title in RIGHTS_DATA:
        RightsStatement.objects.create(
            aquila_id=aquila_id,
            title=title)


def create_packages():
    for title, av_number, duration_access, duration_master, multiple_masters, refid, type in PACKAGE_DATA:
        Package.objects.create(
            title=title,
            av_number=av_number,
            duration_access=duration_access,
            duration_master=duration_master,
            multiple_masters=multiple_masters,
            refid=refid,
            type=type,
            process_status=Package.PENDING)


def copy_binaries():
    """Moves binary files into place."""
    for refid in ['9ba10e5461d401517b0e1a53d514ec87', 'f7d3dd6dc9c4732fa17dbd88fbe652b6']:
        shutil.copytree(
            Path("package_review", FIXTURE_DIR, "packages", refid),
            Path(settings.BASE_STORAGE_DIR, refid),
            dirs_exist_ok=True)


class ArchivesSpaceClientTests(TestCase):

    @patch('asnake.aspace.ASpace.__init__')
    def setUp(self, mock_init):
        mock_init.return_value = None
        self.as_client = ArchivesSpaceClient(
            username='admin',
            password='admin',
            baseurl='https://archivesspace.org/api',
            repository='2')

    def test_init(self):
        """Asserts repository identifier is correctly set."""
        self.assertEqual(
            self.as_client.repository,
            '2')

    def test_get_av_number(self):
        """Asserts AV number is parsed correctly."""
        instances = [{"sub_container": {"indicator_2": "AV 1234"}}, {"sub_container": {}}]
        av_number = self.as_client.get_av_number(instances)
        self.assertEqual(av_number, "AV 1234")


class AWSClientTests(TestCase):

    def setUp(self):
        create_packages()

    @mock_sns
    @mock_sqs
    @mock_sts
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_deliver_message(self, mock_client):
        sns = boto3.client('sns', region_name='us-east-1')
        mock_client.return_value = sns
        topic_arn = sns.create_topic(Name='my-topic')['TopicArn']
        sqs_conn = boto3.resource("sqs", region_name="us-east-1")
        sqs_conn.create_queue(QueueName="test-queue")
        sns.subscribe(
            TopicArn=topic_arn,
            Protocol="sqs",
            Endpoint=f"arn:aws:sqs:us-east-1:{DEFAULT_ACCOUNT_ID}:test-queue")

        client = AWSClient('sns', settings.AWS['role_arn'])

        package = random.choice(Package.objects.all())

        client.deliver_message(
            topic_arn,
            package,
            "This is a message",
            "SUCCESS",
            "1,2")

        queue = sqs_conn.get_queue_by_name(QueueName="test-queue")
        messages = queue.receive_messages(MaxNumberOfMessages=1)
        message_body = json.loads(messages[0].body)
        self.assertEqual(message_body['MessageAttributes']['format']['Value'], package.get_type_display())
        self.assertEqual(message_body['MessageAttributes']['outcome']['Value'], 'SUCCESS')
        self.assertEqual(message_body['MessageAttributes']['refid']['Value'], package.refid)
        self.assertEqual(message_body['MessageAttributes']['rights_ids']['Value'], "1,2")


class DiscoverPackagesCommandTests(TestCase):

    def setUp(self):
        copy_binaries()

    def test_get_type(self):
        """Asserts correct types are returned."""
        for (refid, expected) in [("9ba10e5461d401517b0e1a53d514ec87", Package.VIDEO), ("f7d3dd6dc9c4732fa17dbd88fbe652b6", Package.AUDIO)]:
            output = discover_packages.Command()._get_type(Path(settings.BASE_STORAGE_DIR, refid))
            self.assertEqual(output, expected)

        with self.assertRaises(Exception):
            discover_packages.Command()._get_type(Path("1234"))

    def test_get_duration(self):
        for (filename, expected) in [("9ba10e5461d401517b0e1a53d514ec87.mp4", 5.759), ("f7d3dd6dc9c4732fa17dbd88fbe652b6.mp3", 27.252)]:
            output = discover_packages.Command()._get_duration([Path(settings.BASE_STORAGE_DIR, filename.split('.')[0], filename)])
            self.assertEqual(output, expected)

    @mock_sts
    @mock_ssm
    @patch('package_review.clients.ArchivesSpaceClient.__init__')
    @patch('package_review.management.commands.discover_packages.Command._get_duration')
    @patch('package_review.management.commands.discover_packages.Command._has_multiple_masters')
    @patch('package_review.clients.ArchivesSpaceClient.get_package_data')
    @patch('package_review.clients.AWSClient.deliver_message')
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_handle(self, mock_client, mock_message, mock_package_data, mock_masters, mock_duration, mock_init):
        """Asserts cron produces expected results."""
        expected_len = len(list(Path(settings.BASE_STORAGE_DIR).iterdir()))
        mock_init.return_value = None
        mock_masters.return_value = False
        mock_duration.return_value = 123.45
        mock_package_data.return_value = 'object_title', 'av_number', 'object_uri', 'resource_title', 'resource_uri'

        discover_packages.Command().handle()
        mock_init.assert_called_once()
        mock_client.assert_called_once()
        mock_message.assert_not_called()
        self.assertEqual(mock_package_data.call_count, expected_len)
        self.assertEqual(Package.objects.all().count(), expected_len)
        for package in Package.objects.all():
            self.assertEqual(package.multiple_masters, False)
            self.assertEqual(package.duration_access, 123.45)
            self.assertEqual(package.duration_master, 123.45)

        discover_packages.Command().handle()
        mock_message.assert_not_called()

    @mock_sns
    @mock_sts
    @mock_ssm
    @patch('package_review.clients.ArchivesSpaceClient.__init__')
    @patch('package_review.clients.ArchivesSpaceClient.get_package_data')
    @patch('package_review.clients.AWSClient.deliver_message')
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_handle_exception(self, mock_client, mock_message, mock_package_data, mock_init):
        """Asserts exceptions while processing packages are handled as expected."""
        expected_len = len(list(Path(settings.BASE_STORAGE_DIR).iterdir()))
        mock_package_data.side_effect = Exception("foo")
        mock_init.return_value = None
        discover_packages.Command().handle()
        self.assertEqual(mock_message.call_count, expected_len)

    def tearDown(self):
        for dir in Path(settings.BASE_STORAGE_DIR).iterdir():
            shutil.rmtree(dir)


class CheckQCStatusCommandTests(TestCase):

    @mock_sns
    @mock_sts
    @mock_ssm
    @patch('package_review.clients.AWSClient.deliver_message')
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_qc_done(self, mock_client, mock_message):
        for dir in Path(settings.BASE_STORAGE_DIR).iterdir():
            shutil.rmtree(dir)
        check_qc_status.Command().handle()
        mock_message.assert_called_once_with(
            settings.AWS['sns_topic'],
            None,
            'No packages left to QC',
            'COMPLETE')

    @mock_sns
    @mock_sts
    @mock_ssm
    @patch('package_review.clients.AWSClient.deliver_message')
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_no_message(self, mock_client, mock_message):
        copy_binaries()
        check_qc_status.Command().handle()
        mock_message.assert_not_called()

    def tearDown(self):
        for dir in Path(settings.BASE_STORAGE_DIR).iterdir():
            shutil.rmtree(dir)


class FetchRightsStatementsCommandTests(TestCase):

    @patch('package_review.clients.AquilaClient.available_rights_statements')
    def test_handle(self, mock_rights):
        """Asserts FetchRights cron only adds new rights statements."""
        rights_statements = [{"id": "1", "title": "foo"}, {"id": "2", "title": "bar"}]
        mock_rights.return_value = rights_statements
        fetch_rights_statements.Command().handle()
        mock_rights.assert_called_once_with()
        self.assertEqual(RightsStatement.objects.all().count(), len(rights_statements))

        fetch_rights_statements.Command().handle()
        self.assertEqual(RightsStatement.objects.all().count(), len(rights_statements))


class ViewMixinTests(TestCase):

    def setUp(self):
        create_rights_statements()
        create_packages()

    def test_rights_statement_mixin(self):
        """Asserts rights statements are inserted in views."""
        package_pk = random.choice(Package.objects.all()).pk
        response = self.client.get(reverse('package-detail', args=[package_pk]))
        self.assertEqual(RightsStatement.objects.all().count(), len(response.context['rights_statements']))

        response = self.client.get(reverse('package-bulk-approve'))
        self.assertEqual(len(RIGHTS_DATA), len(response.context['rights_statements']))

    def test_bulk_action_list_mixin(self):
        """Asserts objects are fetched from URL params."""
        form_data = "&".join([f'{str(obj.pk)}=on' for obj in Package.objects.all()])
        for view_str in ['package-bulk-approve', 'package-bulk-reject']:
            response = self.client.get(f'{reverse(view_str)}?{form_data}')
            self.assertEqual(Package.objects.all().count(), len(response.context['object_list']))


class PackageActionViewTests(TestCase):

    def setUp(self):
        create_rights_statements()
        create_packages()
        copy_binaries()
        if Path(settings.BASE_DESTINATION_DIR).exists():
            shutil.rmtree(Path(settings.BASE_DESTINATION_DIR))

    @patch('package_review.clients.AWSClient.__init__')
    @patch('package_review.clients.AWSClient.deliver_message')
    def test_approve_view(self, mock_deliver, mock_init):
        mock_init.return_value = None
        pkg_list = ",".join([str(obj.id) for obj in Package.objects.all()])
        rights_list = ",".join([str(obj.id) for obj in RightsStatement.objects.all()])
        response = self.client.post(f'{reverse("package-approve")}?object_list={pkg_list}&rights_ids={rights_list}')
        self.assertEqual(mock_deliver.call_count, Package.objects.all().count())
        for package in Package.objects.all():
            self.assertEqual(package.process_status, Package.APPROVED)
            self.assertEqual(package.rights_ids, rights_list)
        self.assertEqual(len(list(Path(settings.BASE_STORAGE_DIR).iterdir())), 0)
        self.assertEqual(len(list(Path(settings.BASE_DESTINATION_DIR).iterdir())), Package.objects.all().count())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('package-list'))

    @patch('package_review.clients.AWSClient.__init__')
    @patch('package_review.clients.AWSClient.deliver_message')
    def test_reject_view(self, mock_delete, mock_init):
        mock_init.return_value = None
        pkg_list = ",".join([str(obj.id) for obj in Package.objects.all()])
        response = self.client.post(f'{reverse("package-reject")}?object_list={pkg_list}')
        self.assertEqual(mock_delete.call_count, Package.objects.all().count())
        for package in Package.objects.all():
            self.assertEqual(package.process_status, Package.REJECTED)
        self.assertTrue(len(list(Path(settings.BASE_STORAGE_DIR).iterdir())) == 0)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('package-list'))

    def tearDown(self):
        if Path(settings.BASE_DESTINATION_DIR).exists():
            shutil.rmtree(Path(settings.BASE_DESTINATION_DIR))


class HealthCheckEndpointTests(TestCase):

    def test_endpoint_response(self):
        resp = self.client.get('/health/')
        self.assertEqual(resp.status_code, 200)
