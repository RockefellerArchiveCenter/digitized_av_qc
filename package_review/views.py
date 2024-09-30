from os import getenv
from pathlib import Path
from shutil import rmtree

from django.conf import settings
from django.shortcuts import redirect
from django.views.generic import DetailView, ListView, TemplateView, View

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
            self.move_files(package)
            aws_client.deliver_message(
                settings.AWS['sns_topic'],
                package,
                self.message,
                self.outcome,
                rights_ids)
            package.process_status = Package.APPROVED
            package.rights_ids = rights_ids
            package.save()
        return redirect('package-list')

    def move_files(self, package):
        """Moves files to packaging directory."""
        bag_path = Path(settings.BASE_STORAGE_DIR, package.refid)
        for fp in bag_path.iterdir():
            new_path = Path(settings.BASE_DESTINATION_DIR, package.refid, fp.name)
            new_path.parent.mkdir(parents=True, exist_ok=True)
            fp.rename(new_path)
        rmtree(bag_path)


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
        """Removes files from storage directory."""
        bag_dir = Path(settings.BASE_STORAGE_DIR, package.refid)
        if bag_dir.exists():
            rmtree(bag_dir)


class PackageDataRefreshView(PackageActionView):
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
