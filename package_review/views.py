from os import getenv

from django.conf import settings
from django.shortcuts import redirect
from django.views.generic import DetailView, ListView, TemplateView, View
from storages.backends.s3boto3 import S3Boto3Storage

from .clients import ArchivesSpaceClient, AWSClient
from .helpers import get_config
from .models import Package, RightsStatement


class RightsStatementMixin(View):
    """Mixin to support fetching rights statements from Aquila."""

    def get_context_data(self, **kwargs):
        """Overrides default method to add rights statements to context."""
        context = super().get_context_data(**kwargs)
        context['rights_statements'] = RightsStatement.objects.all()
        return context


class PackageListView(ListView):
    """List view for packages waiting to be reviewed."""
    template_name = 'list.html'
    model = Package
    queryset = Package.objects.filter(process_status=Package.PENDING)


class PackageDetailView(RightsStatementMixin, DetailView):
    """Detail view for individual packages."""
    template_name = 'detail.html'
    model = Package

    def get_context_data(self, **kwargs):
        """Adds PDF URL to context."""
        context = super().get_context_data(**kwargs)
        s3_storage = S3Boto3Storage()
        suffix = '.mp3' if self.object.type == Package.AUDIO else '.mp4'
        context['asset_url'] = s3_storage.url(f'{self.object.refid}/{self.object.refid}{suffix}')
        return context


class BulkActionListView(View):
    """List page for items on which bulk action will be taken."""

    def get_context_data(self, **kwargs):
        """Parses object list from object_list query param."""
        context = super().get_context_data(**kwargs)
        object_ids = [int(k) for k in self.request.GET]
        context['object_list'] = Package.objects.filter(pk__in=object_ids)
        return context


class PackageBulkRejectView(BulkActionListView, TemplateView):
    """List page for items awaiting bulk rejection."""
    template_name = 'bulk_reject.html'


class PackageBulkApproveView(RightsStatementMixin, BulkActionListView, TemplateView):
    """List view for items awaiting bulk approval."""
    template_name = 'bulk_approve.html'


class PackageActionView(View):
    """Handles approval or rejection of a list of packages."""

    def _get_queryset(self, request):
        """Parses URL parameters to return queryset."""
        object_ids = [int(pk) for pk in request.GET['object_list'].split(',')]
        return Package.objects.filter(pk__in=object_ids)


class PackageApproveView(PackageActionView):
    """Approves a list of packages."""
    message = 'Package reviewed and approved.'
    outcome = 'SUCCESS'

    def post(self, request, *args, **kwargs):
        queryset = self._get_queryset(request)
        rights_ids = request.GET['rights_ids']
        aws_client = AWSClient('sns', settings.AWS['role_arn'])
        for package in queryset:
            package.process_status = Package.APPROVED
            package.rights_ids = rights_ids
            package.save()
            aws_client.deliver_message(
                settings.AWS['sns_topic'],
                package,
                self.message,
                self.outcome,
                rights_ids=rights_ids,
                size=package.size_bytes)
        return redirect('package-list')


class PackageRejectView(PackageActionView):
    """Rejects a list of packages."""
    message = 'Package reviewed and rejected.'
    outcome = 'FAILURE'

    def post(self, request, *args, **kwargs):
        queryset = self._get_queryset(request)
        aws_client = AWSClient('sns', settings.AWS['role_arn'])
        for package in queryset:
            self.delete_files(package)
            aws_client.deliver_message(
                settings.AWS['sns_topic'],
                package,
                self.message,
                self.outcome)
            package.process_status = Package.REJECTED
            package.save()
        return redirect('package-list')

    def delete_files(self, package):
        """Removes files from storage bucket."""
        s3_client = AWSClient('s3', settings.AWS['role_arn'])
        paginator = s3_client.client.get_paginator('list_objects_v2')
        pages = paginator.paginate(Bucket=settings.AWS['bucket'], Prefix=package.refid)

        objects_to_delete = []
        for page in pages:
            if 'Contents' in page:
                for obj in page['Contents']:
                    objects_to_delete.append({'Key': obj['Key']})

        if objects_to_delete:
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i:i + 1000]
                response = s3_client.client.delete_objects(
                    Bucket=settings.AWS['bucket'],
                    Delete={'Objects': batch, 'Quiet': True})
                if 'Errors' in response:
                    errors = "\n".join([e["Key"] for e in response["errors"]])
                    raise Exception(f'Error deleting objects: {errors}')


class PackageDataRefreshView(PackageActionView):
    """Updates ArchivesSpace data for a package."""

    def get(self, request, *args, **kwargs):
        queryset = self._get_queryset(request)
        configuration = get_config(f"/{getenv('ENV')}/{getenv('APP_CONFIG_PATH')}")
        client = ArchivesSpaceClient(
            baseurl=configuration.get('AS_BASEURL'),
            username=configuration.get('AS_USERNAME'),
            password=configuration.get('AS_PASSWORD'),
            repository=configuration.get('AS_REPO'))
        for package in queryset:
            title, av_number, uri, resource_title, resource_uri, undated_object = client.get_package_data(package.refid)
            package.title = title
            package.av_number = av_number
            package.uri = uri
            package.resource_title = resource_title
            package.resource_uri = resource_uri
            package.undated_object = undated_object
            package.save()
        return redirect('package-detail', pk=package.pk)


class PackageTreeUpdateView(PackageActionView):
    """Updates the directory tree for a package."""

    def get(self, request, *args, **kwargs):
        queryset = self._get_queryset(request)
        s3_client = AWSClient('s3', settings.AWS['role_arn'])
        paginator = s3_client.client.get_paginator('list_objects_v2')
        for package in queryset:
            objects = []
            pages = paginator.paginate(Bucket=settings.AWS['bucket'], Prefix=package.refid)
            for page in pages:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        objects.append(obj['Key'])
                package.tree = "\n".join([obj for obj in sorted(objects)])
            package.save()
        return redirect('package-detail', pk=package.pk)
