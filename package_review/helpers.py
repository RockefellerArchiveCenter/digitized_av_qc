from django.conf import settings

from .clients import AWSClient


def get_config(parameter_path):
    ssm_client = AWSClient('ssm', settings.AWS['role_arn']).client
    configuration = {}
    param_details = ssm_client.get_parameters_by_path(
        Path=parameter_path,
        Recursive=False,
        WithDecryption=True)
    for param in param_details.get('Parameters', []):
        param_path_array = param.get('Name').split("/")
        section_name = param_path_array[-1]
        configuration[section_name] = param.get('Value')
    return configuration
