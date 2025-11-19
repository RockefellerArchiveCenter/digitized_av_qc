import json
import random
from os import getenv
from pathlib import Path
from unittest.mock import patch

import boto3
from django.conf import settings
from django.shortcuts import reverse
from django.test import TestCase
from moto import mock_aws
from moto.core import DEFAULT_ACCOUNT_ID

from .clients import ArchivesSpaceClient, AWSClient
from .helpers import get_config
from .management.commands import (check_qc_status, discover_packages,
                                  fetch_rights_statements)
from .models import Package, RightsStatement

FIXTURE_DIR = "fixtures"
RIGHTS_DATA = [("1", "foo"), ("2", "bar")]
PACKAGE_DATA = [("foo", "av 123", 123.45, 123.45, False, False, "9ba10e5461d401517b0e1a53d514ec87", Package.AUDIO),
                ("bar", "av 321", 543.21, 543.21, True, True, "f7d3dd6dc9c4732fa17dbd88fbe652b6", Package.VIDEO)]


def create_rights_statements():
    for aquila_id, title in RIGHTS_DATA:
        RightsStatement.objects.create(
            aquila_id=aquila_id,
            title=title)


def create_packages(status=Package.PENDING):
    for title, av_number, duration_access, duration_master, multiple_masters, undated_object, refid, type in PACKAGE_DATA:
        Package.objects.create(
            title=title,
            av_number=av_number,
            duration_access=duration_access,
            duration_master=duration_master,
            multiple_masters=multiple_masters,
            undated_object=undated_object,
            refid=refid,
            type=type,
            process_status=status)


def upload_bag(s3, bag_path):
    for dirpath, _, files in (bag_path).walk():
        for f in files:
            source = dirpath / f
            destination = source.relative_to(bag_path.parent)
            s3.upload_file(
                str(source),
                settings.AWS['bucket'],
                str(destination))


class HelpersTests(TestCase):

    @mock_aws
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_get_config(self, mock_client):
        """Asserts configs are properly fetched from SSM"""
        ssm = boto3.client('ssm', region_name='us-east-1')
        mock_client.return_value = ssm
        path = "/dev/digitized-av-qc"
        for name, value in [("foo", "bar"), ("baz", "buzz")]:
            ssm.put_parameter(
                Name=f"{path}/{name}",
                Value=value,
                Type="SecureString",
            )
        config = get_config(path)
        self.assertIsInstance(config, dict)
        self.assertEqual(config, {'foo': 'bar', 'baz': 'buzz'})


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

    def test_has_structured_dates(self):
        """Asserts presence of structured dates are parsed correctly."""
        for dates, expected in [
            ([], False),
            ([{
                "expression": "1950",
                "date_type": "single"
            }], False),
            ([{
                "begin": "1950",
                "end": "1969",
                "date_type": "inclusive",
            }], True),
            ([{
                "begin": "1950",
                "date_type": "single"
            }], True)
        ]:
            output = self.as_client.has_structured_dates(dates)
            self.assertEqual(output, expected)


class AWSClientTests(TestCase):

    def setUp(self):
        create_packages()

    @mock_aws
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
            rights_ids="1,2")

        queue = sqs_conn.get_queue_by_name(QueueName="test-queue")
        messages = queue.receive_messages(MaxNumberOfMessages=1)
        message_body = json.loads(messages[0].body)
        self.assertEqual(message_body['MessageAttributes']['format']['Value'], package.get_type_display())
        self.assertEqual(message_body['MessageAttributes']['outcome']['Value'], 'SUCCESS')
        self.assertEqual(message_body['MessageAttributes']['refid']['Value'], package.refid)
        self.assertEqual(message_body['MessageAttributes']['rights_ids']['Value'], "1,2")

    @mock_aws
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_calculate_package_size(self, mock_client):
        s3 = boto3.client('s3', region_name='us-east-1')
        mock_client.return_value = s3
        s3.create_bucket(Bucket=settings.AWS['bucket'])
        file_content = "Garbage content for test file!"  # 30 bytes
        package_id = "123456789"
        for x in range(5):
            s3.put_object(
                Bucket=settings.AWS['bucket'],
                Key=f'{package_id}/{x}/file.txt',
                Body=file_content)

        client = AWSClient('s3', settings.AWS['role_arn'])

        size = client.calculate_package_size(package_id)
        self.assertEqual(size, 150)

    @mock_aws
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_key_exists(self, mock_client):
        s3 = boto3.client('s3', region_name='us-east-1')
        mock_client.return_value = s3
        s3.create_bucket(Bucket=settings.AWS['bucket'])
        file_content = "Garbage content for test file!"
        file_name = 'file.txt'
        s3.put_object(
            Bucket=settings.AWS['bucket'],
            Key=file_name,
            Body=file_content)

        client = AWSClient('s3', settings.AWS['role_arn'])

        self.assertTrue(client.key_exists(settings.AWS['bucket'], file_name))
        self.assertFalse(client.key_exists(settings.AWS['bucket'], 'foo'))

    @mock_aws
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_get_signed_urls(self, mock_client):
        s3 = boto3.client('s3', region_name='us-east-1')
        mock_client.return_value = s3
        s3.create_bucket(Bucket=settings.AWS['bucket'])
        file_content = "Garbage content for test file!"
        package_id = "123456789"
        for x in range(5):
            s3.put_object(
                Bucket=settings.AWS['bucket'],
                Key=f'{package_id}/{x}/file.txt',
                Body=file_content)

        client = AWSClient('s3', settings.AWS['role_arn'])

        urls = client.get_signed_urls(package_id, settings.AWS['bucket'], '.txt')
        self.assertEqual(len(urls), 5)

        urls = client.get_signed_urls(package_id, settings.AWS['bucket'], '.mp3')
        self.assertEqual(len(urls), 0)


class DiscoverPackagesCommandTests(TestCase):

    @mock_aws
    @patch('package_review.clients.AWSClient.get_client_with_role')
    @patch('package_review.clients.AWSClient.key_exists')
    def test_get_type(self, mock_exists, mock_client):
        """Asserts correct types are returned."""
        mock_exists.return_value = True
        s3 = boto3.client('s3', region_name='us-east-1')
        mock_client.return_value = s3
        bucket_name = 'foo'
        refid = '9ba10e5461d401517b0e1a53d514ec87'
        s3_client = AWSClient('s3', settings.AWS['role_arn'])

        output = discover_packages.Command()._get_type(refid, bucket_name, s3_client)
        self.assertEqual(output, Package.AUDIO)

        mock_exists.side_effect = [False, True]
        output = discover_packages.Command()._get_type(refid, bucket_name, s3_client)
        self.assertEqual(output, Package.VIDEO)

        mock_exists.side_effect = [False, False]
        with self.assertRaises(Exception) as e:
            discover_packages.Command()._get_type(refid, bucket_name, s3_client)
        self.assertEqual(str(e.exception), f"Unable to determine type of package {refid}")

    @mock_aws
    @patch('package_review.clients.ArchivesSpaceClient.__init__')
    @patch('package_review.management.commands.discover_packages.Command._get_duration')
    @patch('package_review.management.commands.discover_packages.Command._has_multiple_masters')
    @patch('package_review.management.commands.discover_packages.get_config')
    @patch('package_review.clients.ArchivesSpaceClient.get_package_data')
    @patch('package_review.clients.AWSClient.deliver_message')
    @patch('package_review.clients.AWSClient.get_signed_urls')
    @patch('package_review.clients.AWSClient.calculate_package_size')
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_handle(self, mock_client, mock_calculate, mock_signed_urls, mock_message, mock_package_data, mock_config, mock_masters, mock_duration, mock_init):
        """Asserts cron produces expected results."""
        mock_init.return_value = None
        mock_masters.return_value = False
        mock_duration.return_value = 123.45
        mock_package_data.return_value = 'object_title', 'av_number', 'object_uri', 'resource_title', 'resource_uri', False
        mock_calculate.return_value = 0
        mock_signed_urls.return_value = []
        refid = "123456789"

        discover_packages.Command().handle(refid=refid)
        mock_init.assert_called_once()
        mock_client.assert_called_once_with('s3', getenv('AWS_ROLE_ARN'))
        mock_message.assert_not_called()
        mock_config.assert_called_once()
        mock_package_data.assert_called_once_with("123456789")
        mock_calculate.assert_called_once_with("123456789")
        self.assertEqual(mock_signed_urls.call_count, 2)
        self.assertEqual(Package.objects.all().count(), 1)
        for package in Package.objects.all():
            self.assertEqual(package.multiple_masters, False)
            self.assertEqual(package.duration_access, 123.45)
            self.assertEqual(package.duration_master, 123.45)

    @mock_aws
    @patch('package_review.clients.ArchivesSpaceClient.__init__')
    @patch('package_review.clients.ArchivesSpaceClient.get_package_data')
    @patch('package_review.clients.AWSClient.deliver_message')
    @patch('package_review.clients.AWSClient.get_client_with_role')
    @patch('package_review.management.commands.discover_packages.get_config')
    def test_handle_exception(self, mock_config, mock_client, mock_message, mock_package_data, mock_init):
        """Asserts exceptions while processing packages are handled as expected."""
        mock_package_data.side_effect = Exception("foo")
        mock_init.return_value = None
        discover_packages.Command().handle(refid="123456789")
        mock_message.assert_called_once()
        self.assertEqual(mock_client.call_count, 2)
        mock_config.assert_called_once()


class CheckQCStatusCommandTests(TestCase):

    @mock_aws
    @patch('package_review.clients.AWSClient.deliver_message')
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_qc_done(self, mock_client, mock_message):
        create_packages(Package.APPROVED)
        check_qc_status.Command().handle()
        mock_message.assert_called_once_with(
            settings.AWS['sns_topic'],
            None,
            'No packages left to QC',
            'COMPLETE')
        mock_client.assert_called_once()

    @mock_aws
    @patch('package_review.clients.AWSClient.deliver_message')
    @patch('package_review.clients.AWSClient.get_client_with_role')
    def test_no_message(self, mock_client, mock_message):
        create_packages()
        check_qc_status.Command().handle()
        mock_message.assert_not_called()
        mock_client.assert_not_called()


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

    @mock_aws
    def test_approve_view(self):
        sns = boto3.client('sns', region_name='us-east-1')
        topic_arn = sns.create_topic(Name='digitized-av-events')['TopicArn']
        sqs_conn = boto3.resource("sqs", region_name="us-east-1")
        sqs_conn.create_queue(QueueName="test-queue")
        sns.subscribe(
            TopicArn=topic_arn,
            Protocol="sqs",
            Endpoint=f"arn:aws:sqs:us-east-1:{DEFAULT_ACCOUNT_ID}:test-queue")
        pkg_list = ",".join([str(obj.id) for obj in Package.objects.all()])
        rights_list = ",".join([str(obj.id) for obj in RightsStatement.objects.all()])

        response = self.client.post(f'{reverse("package-approve")}?object_list={pkg_list}&rights_ids={rights_list}')

        for package in Package.objects.all():
            self.assertEqual(package.process_status, Package.APPROVED)
            self.assertEqual(package.rights_ids, rights_list)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('package-list'))

        queue = sqs_conn.get_queue_by_name(QueueName="test-queue")
        messages = queue.receive_messages(MaxNumberOfMessages=5)
        self.assertEqual(len(Package.objects.all()), len(messages))

    @mock_aws
    @patch('package_review.clients.AWSClient.deliver_message')
    def test_reject_view(self, mock_delete):
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket=settings.AWS['bucket'])
        for refid in ['9ba10e5461d401517b0e1a53d514ec87', 'f7d3dd6dc9c4732fa17dbd88fbe652b6']:
            bag_path = Path("package_review", FIXTURE_DIR, "packages", refid)
            upload_bag(s3, bag_path)

        pkg_list = ",".join([str(obj.id) for obj in Package.objects.all()])

        response = self.client.post(f'{reverse("package-reject")}?object_list={pkg_list}')

        self.assertEqual(mock_delete.call_count, Package.objects.all().count())
        for package in Package.objects.all():
            self.assertEqual(package.process_status, Package.REJECTED)
        found = s3.list_objects_v2(
            Bucket=settings.AWS['bucket'],
            MaxKeys=1)['KeyCount']
        assert found == 0
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('package-list'))

    @patch('package_review.clients.ArchivesSpaceClient.__init__')
    @patch('package_review.clients.ArchivesSpaceClient.get_package_data')
    @patch('package_review.views.get_config')
    def test_refresh_view(self, mock_config, mock_data, mock_init):
        mock_init.return_value = None
        title = "title"
        av_number = "AV 1234"
        object_uri = "/repositories/2/archival_objects/1"
        resource_title = "resource title"
        resource_uri = "/repositories/2/resources/1"
        undated_object = True
        mock_data.return_value = title, av_number, object_uri, resource_title, resource_uri, undated_object
        package = random.choice(Package.objects.all())
        response = self.client.get(f'{reverse("refresh-data")}?object_list={package.id}')
        package.refresh_from_db()
        self.assertEqual(package.title, title)
        self.assertEqual(package.av_number, av_number)
        self.assertEqual(package.uri, object_uri)
        self.assertEqual(package.resource_title, resource_title)
        self.assertEqual(package.resource_uri, resource_uri)
        self.assertEqual(package.undated_object, undated_object)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('package-detail', kwargs={'pk': package.pk}))

    @mock_aws
    def test_update_tree(self):
        refid = '9ba10e5461d401517b0e1a53d514ec87'
        s3 = boto3.client('s3', region_name='us-east-1')
        s3.create_bucket(Bucket=settings.AWS['bucket'])
        bag_path = Path("package_review", FIXTURE_DIR, "packages", refid)
        upload_bag(s3, bag_path)

        package = Package.objects.get(refid=refid)
        response = self.client.get(f'{reverse("update-tree")}?object_list={package.id}')
        package.refresh_from_db()
        print(package.tree)
        self.assertEqual(
            package.tree,
            "9ba10e5461d401517b0e1a53d514ec87/9ba10e5461d401517b0e1a53d514ec87.mp4"
        )

        self.assertEqual(response.url, reverse('package-detail', kwargs={'pk': package.pk}))


class HealthCheckEndpointTests(TestCase):

    def test_endpoint_response(self):
        resp = self.client.get('/health/')
        self.assertEqual(resp.status_code, 200)
