from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.decorators import user_passes_test
from django.views.generic import RedirectView

# Superuser check for admin
def superuser_only(view_func):
    return user_passes_test(lambda u: u.is_superuser, login_url='/hr-login/')(view_func)

urlpatterns = [
    # Superuser ONLY - Django admin panel (only accessible by superusers)
    path('admin/', superuser_only(admin.site.urls)),
    
    # All other URLs go to users app (which has @hr_required and @admin_required decorators)
    path('', include('users.urls')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)