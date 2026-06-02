"""
Django settings for mylife project.
"""

from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv
from datetime import timedelta

# Load environment variables
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# ================= SECURITY =================
# SECRET KEY - No fallback in production for security
SECRET_KEY = os.environ.get('SECRET_KEY')
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

# In production, SECRET_KEY must be set
if not DEBUG and not SECRET_KEY:
    raise ValueError("SECRET_KEY must be set in production environment!")

# Development fallback only
if not SECRET_KEY:
    SECRET_KEY = 'django-insecure-abc123def456ghi789jkl012mno345pqr678stu'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# ================= APPLICATIONS =================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'users',
]

# ================= MIDDLEWARE =================
MIDDLEWARE = [
    'django.middleware.gzip.GZipMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# ================= URL CONFIG =================
ROOT_URLCONF = 'mylife.urls'

# ================= TEMPLATES =================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ================= WSGI =================
WSGI_APPLICATION = 'mylife.wsgi.application'

# ================= DATABASE - Railway PostgreSQL =================
# Use DATABASE_URL environment variable (Railway sets this automatically)
DATABASES = {
    'default': dj_database_url.config(
        default='sqlite:///db.sqlite3',
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# ================= PASSWORD VALIDATION =================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ================= INTERNATIONALIZATION =================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Dar_es_Salaam'
USE_I18N = True
USE_TZ = True

# ================= STATIC FILES =================
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ================= MEDIA FILES =================
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ================= DEFAULT PRIMARY KEY FIELD =================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ================= AUTHENTICATION =================
# Login URLs for different user types
LOGIN_URL = '/hr-login/'           # HR users login URL
LOGOUT_REDIRECT_URL = '/hr-login/' # After logout redirect
LOGIN_REDIRECT_URL = '/hr-dashboard/'  # After successful HR login

# Admin/Superuser login (uses Django's default)
# Admin panel is at /admin/

# ================= EMAIL =================
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER

# ================= CORS =================
CORS_ALLOW_ALL_ORIGINS = True  # Set to False in production with specific origins
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ['DELETE', 'GET', 'OPTIONS', 'PATCH', 'POST', 'PUT']
CORS_ALLOW_HEADERS = [
    'accept', 'accept-encoding', 'authorization', 'content-type',
    'dnt', 'origin', 'user-agent', 'x-csrftoken', 'x-requested-with',
    'x-idempotency-key'
]

# ================= CSRF SETTINGS =================
CSRF_COOKIE_NAME = 'csrftoken'
CSRF_HEADER_NAME = 'HTTP_X_CSRFTOKEN'
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:8000',
    'http://127.0.0.1:8000',
    'https://*.onrender.com',
    'https://hr-attendance-system-gojk.onrender.com',
    'https://*.up.railway.app',  # Railway domain
]

# ================= REST FRAMEWORK =================
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': ('rest_framework.permissions.AllowAny',),
    'DEFAULT_RENDERER_CLASSES': ('rest_framework.renderers.JSONRenderer',),
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {'anon': '100/day', 'user': '1000/day'},
}

# ================= JWT SETTINGS =================
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# ================= SESSION SETTINGS (FIXED FOR MULTIPLE SESSIONS) =================
# These settings allow users to be logged in on multiple devices/networks simultaneously
SESSION_COOKIE_AGE = 86400  # Changed: 24 hours (was 3600 = 1 hour)
SESSION_SAVE_EVERY_REQUEST = True  # Keeps session alive with each request
SESSION_EXPIRE_AT_BROWSER_CLOSE = False  # Changed: False so session persists after closing browser

# These settings are critical for multiple sessions on different networks
# Session cookie is NOT tied to IP address, so user can switch networks
SESSION_COOKIE_HTTPONLY = True  # Prevents JavaScript access (security)
SESSION_COOKIE_SAMESITE = 'Lax'  # Allows cross-site requests

# ================= SECURITY HEADERS (Production Only) =================
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_SSL_REDIRECT = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 47304000  # 1.5 years
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True