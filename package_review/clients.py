import boto3
from asnake.aspace import ASpace
from aws_assume_role_lib import assume_role
from requests import Session


class ArchivesSpaceClient(ASpace):
    """Client to interact with ArchivesSpace API."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.repository = kwargs['repository']

    def get_av_number(self, instances):
        """Parse the AV number for an ArchivesSpace object.

        Args:
            instances (list): ArchivesSpace instance data.

        Returns:
            av_number (string): AV number for the object.
        """
        av_numbers = [instance.get('sub_container', {}).get('indicator_2') for instance in instances if instance.get('sub_container', {}).get('indicator_2', '').startswith('AV')]
        return ', '.join(number for number in av_numbers if number is not None)

    def get_package_data(self, refid):
        """Fetch data about an object in ArchivesSpace.

        Args:
            refid (string): RefID for an ArchivesSpace archival object.

        Returns:
            object_title, av_number, object_uri, resource_title, resource_uri (tuple of strings): data about the object.
        """
        results = self.client.get(f"/repositories/{self.repository}/find_by_id/archival_objects?ref_id[]={refid}&resolve[]=archival_objects&resolve[]=archival_objects::resource").json()
        try:
            if len(results['archival_objects']) != 1:
                raise Exception(f'Expecting to get one result for ref id {refid} but got {len(results["archival_objects"])} instead.')
            object = results['archival_objects'][0]['_resolved']
            av_number = self.get_av_number(object['instances'])
            object_uri = object['uri']
            resource = object['resource']['_resolved']
            resource_title = resource['title']
            resource_uri = resource['uri']
            return object['display_string'], av_number, object_uri, resource_title, resource_uri
        except KeyError:
            raise Exception(f'Unable to fetch results for {refid}. Got results {results}')


class AquilaClient(object):

    def __init__(self, baseurl):
        self.baseurl = baseurl.rstrip("/")
        self.client = Session()

    def available_rights_statements(self):
        """Fetches available rights statements from Aquila.

        Returns:
            rights_statements (list of tuples): IDs and display strings of rights statements
        """
        return self.client.get(f'{self.baseurl}/api/rights/').json()


class AWSClient(object):

    def __init__(self, resource, role_arn):
        """Gets Boto3 client which authenticates with a specific IAM role."""
        self.client = self.get_client_with_role(resource, role_arn)

    def get_client_with_role(self, resource, role_arn):
        """Gets Boto3 client which authenticates with a specific IAM role."""
        session = boto3.Session()
        assumed_role_session = assume_role(session, role_arn)
        return assumed_role_session.client(resource)

    def deliver_message(self, sns_topic, package, message, outcome, rights_ids=None):
        """Delivers message to SNS Topic."""
        attributes = {
            'service': {
                'DataType': 'String',
                'StringValue': 'digitized_av_qc',
            },
            'outcome': {
                'DataType': 'String',
                'StringValue': outcome,
            }
        }
        if package:
            attributes['format'] = {
                'DataType': 'String',
                'StringValue': package.get_type_display(),
            }
            attributes['refid'] = {
                'DataType': 'String',
                'StringValue': package.refid,
            }
        if rights_ids:
            attributes['rights_ids'] = {
                'DataType': 'String',
                'StringValue': rights_ids,
            }
        self.client.publish(
            TopicArn=sns_topic,
            Message=message,
            MessageAttributes=attributes)
