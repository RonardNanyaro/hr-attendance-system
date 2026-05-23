# idempotency.py
import json
from functools import wraps
from django.utils import timezone
from django.http import JsonResponse
from datetime import timedelta
from .models import IdempotencyKey


def idempotent(key_func=None, ttl_hours=24):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            idempotency_key = key_func(request) if key_func else request.headers.get('X-Idempotency-Key')
            if not idempotency_key:
                return view_func(request, *args, **kwargs)
            
            request_type = request.resolver_match.url_name if request.resolver_match else 'unknown'
            full_key = f"{request_type}:{idempotency_key}"
            user_id = request.user.id if request.user.is_authenticated else None
            
            try:
                existing = IdempotencyKey.objects.get(key=full_key, expires_at__gt=timezone.now())
                return JsonResponse(existing.response_data, status=existing.status_code)
            except IdempotencyKey.DoesNotExist:
                pass
            
            response = view_func(request, *args, **kwargs)
            
            if 200 <= response.status_code < 300:
                try:
                    if hasattr(response, 'content'):
                        response_data = json.loads(response.content.decode('utf-8'))
                    else:
                        response_data = {'success': True}
                    
                    IdempotencyKey.objects.create(
                        key=full_key,
                        request_type=request_type,
                        user_id=user_id,
                        response_data=response_data,
                        status_code=response.status_code,
                        expires_at=timezone.now() + timedelta(hours=ttl_hours)
                    )
                except Exception as e:
                    print(f"Idempotency error: {e}")
            return response
        return wrapper
    return decorator

# users/management/commands/cleanup_idempotency.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from users.models import IdempotencyKey


class Command(BaseCommand):
    help = 'Clean up expired idempotency keys'
    
    def handle(self, *args, **options):
        deleted = IdempotencyKey.objects.filter(
            expires_at__lt=timezone.now()
        ).delete()
        self.stdout.write(
            self.style.SUCCESS(f'Deleted {deleted[0]} expired idempotency keys')
        )