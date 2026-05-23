from django.core.exceptions import PermissionDenied
from .models import Profile


def hr_required(view_func):
    """Decorator to check if user is an approved HR"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied
        
        try:
            profile = Profile.objects.get(user=request.user)
        except Profile.DoesNotExist:
            raise PermissionDenied
        
        if profile.role != 'hr' or profile.status != 'approved':
            raise PermissionDenied
        
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_required(view_func):
    """Decorator to check if user is super admin"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


def employee_required(view_func):
    """Decorator to check if user is an approved employee (for mobile API)"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied
        
        try:
            profile = Profile.objects.get(user=request.user)
        except Profile.DoesNotExist:
            raise PermissionDenied
        
        if profile.role != 'employee' or profile.status != 'approved':
            raise PermissionDenied
        
        return view_func(request, *args, **kwargs)
    return wrapper