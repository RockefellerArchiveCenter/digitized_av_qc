"""
URL configuration for digitized_av_qc project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import re_path

from package_review.views import (PackageApproveView, PackageBulkApproveView,
                                  PackageBulkRejectView, PackageDetailView,
                                  PackageListView, PackageRejectView)

urlpatterns = [
    # path("admin/", admin.site.urls),
    re_path(r'^$', PackageListView.as_view(), name='package-list'),
    re_path(r'^package/(?P<pk>[\d]+)/$', PackageDetailView.as_view(), name='package-detail'),
    re_path(r'^package/bulk-approve/$', PackageBulkApproveView.as_view(), name='package-bulk-approve'),
    re_path(r'^package/bulk-reject/$', PackageBulkRejectView.as_view(), name='package-bulk-reject'),
    re_path(r'^package/approve/', PackageApproveView.as_view(), name='package-approve'),
    re_path(r'^package/reject/', PackageRejectView.as_view(), name='package-reject'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
