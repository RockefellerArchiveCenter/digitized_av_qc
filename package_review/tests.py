import json
import random
import shutil
from pathlib import Path
from unittest.mock import patch

import boto3
from django.conf import settings
from django.shortcuts import reverse
from django.test import TestCase
from moto import mock_sns, mock_sqs, mock_sts
from moto.core import DEFAULT_ACCOUNT_ID

from .clients import ArchivesSpaceClient, AWSClient
from .cron import DiscoverPackages, FetchRightsStatements
from .models import Package, RightsStatement

FIXTURE_DIR = "fixtures"
RIGHTS_DATA = [("1", "foo"), ("2", "bar")]
PACKAGE_DATA = [("foo", "av 123", "9ba10e5461d401517b0e1a53d514ec87", Package.AUDIO),
                ("bar", "av 321", "f7d3dd6dc9c4732fa17dbd88fbe652b6", Package.VIDEO)]


def create_rights_statements():
    for aquila_id, title in RIGHTS_DATA:
        RightsStatement.objects.create(
            aquila_id=aquila_id,
            title=title)


def create_packages():
    for title, av_number, refid, type in PACKAGE_DATA:
        Package.objects.create(
            title=title,
            av_number=av_number,
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
        self.client = ArchivesSpaceClient(
            username=settings.ARCHIVESSPACE['username'],
            password=settings.ARCHIVESSPACE['password'],
            baseurl=settings.ARCHIVESSPACE['baseurl'],
            repository=settings.ARCHIVESSPACE['repository'])

    def test_init(self):
        """Asserts repository identifier is correctly set."""
        self.assertEqual(
            self.client.repository,
            settings.ARCHIVESSPACE['repository'])

    def test_get_av_number(self):
        """Asserts AV number is parsed correctly."""
        instances = [{"sub_container": {"indicator_2": "AV 1234"}}, {"sub_container": {}}]
        av_number = self.client.get_av_number(instances)
        self.assertEqual(av_number, "AV 1234")


class AWSClientTests(TestCase):

    def setUp(self):
        create_packages()

    @mock_sns
    @mock_sqs
    @mock_sts
    def test_deliver_message(self):
        sns = boto3.client('sns', region_name='us-east-1')
        topic_arn = sns.create_topic(Name='my-topic')['TopicArn']
        sqs_conn = boto3.resource("sqs", region_name="us-east-1")
        sqs_conn.create_queue(QueueName="test-queue")
        sns.subscribe(
            TopicArn=topic_arn,
            Protocol="sqs",
            Endpoint=f"arn:aws:sqs:us-east-1:{DEFAULT_ACCOUNT_ID}:test-queue",
        )

        client = AWSClient(
            'sns',
            settings.AWS['access_key_id'],
            settings.AWS['secret_access_key'],
            settings.AWS['region'],
            settings.AWS['role_arn']
        )

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


class DiscoverPackagesCronTests(TestCase):

    def setUp(self):
        copy_binaries()

    def test_get_type(self):
        """Asserts correct types are returned."""
        for (refid, expected) in [("9ba10e5461d401517b0e1a53d514ec87", Package.VIDEO), ("f7d3dd6dc9c4732fa17dbd88fbe652b6", Package.AUDIO)]:
            output = DiscoverPackages()._get_type(Path(settings.BASE_STORAGE_DIR, refid))
            self.assertEqual(output, expected)

        with self.assertRaises(Exception):
            DiscoverPackages()._get_type(Path("1234"))

    @patch('package_review.clients.ArchivesSpaceClient.__init__')
    @patch('package_review.clients.ArchivesSpaceClient.get_package_data')
    def test_do(self, mock_package_data, mock_init):
        """Asserts cron produces expected results."""
        expected_len = len(list(Path(settings.BASE_STORAGE_DIR).iterdir()))
        mock_init.return_value = None
        mock_package_data.return_value = "foo", "bar"
        output = DiscoverPackages().do()
        self.assertTrue(isinstance(output, str))
        mock_init.assert_called_once()
        self.assertEqual(mock_package_data.call_count, expected_len)
        self.assertEqual(Package.objects.all().count(), expected_len)

    def tearDown(self):
        for dir in Path(settings.BASE_STORAGE_DIR).iterdir():
            shutil.rmtree(dir)


class FetchRightsStatementsCronTests(TestCase):

    @patch('package_review.clients.AquilaClient.available_rights_statements')
    def test_do(self, mock_rights):
        """Asserts FetchRights cron only adds new rights statements."""
        rights_statements = [{"id": "1", "title": "foo"}, {"id": "2", "title": "bar"}]
        mock_rights.return_value = rights_statements
        FetchRightsStatements().do()
        mock_rights.assert_called_once_with()
        self.assertEqual(RightsStatement.objects.all().count(), len(rights_statements))

        FetchRightsStatements().do()
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
