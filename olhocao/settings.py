"""
Django settings for olhocao project.

Generated by 'django-admin startproject' using Django 5.2.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.2/ref/settings/
"""
import environ

from pathlib import Path
from django.utils.translation import gettext_lazy as _

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


env = environ.Env(
    # <key>=(<casting>, <default-value>),
    SECRET_KEY=(
        str,
        'django-insecure-l1!^z!o4nd36@)2pd-yjl&#q1fx7x1f%(*9cgf*+7b54_)v)-c'
    ),

    DEBUG=(bool, False),

    EMAIL_HOST=(str, 'localhost'),
    EMAIL_PORT=(int, 25),
    EMAIL_HOST_USER=(str, ''),
    EMAIL_HOST_PASSWORD=(str, ''),
    EMAIL_USE_TLS=(bool, False),
    EMAIL_USE_SSL=(bool, False),

    DEFAULT_FROM_EMAIL=(str, 'no-reply@olhocaocentrocanino.com'),

    RECIPIENT_LIST_ON_CONTACT_US_REQUEST=(
        list, ['geral@olhocaocentrocanino.com', 'luiscosta2001@gmail.com']
    ),

    ALLOWED_HOSTS=(list, ['*']),

    DATABASE_URL=(str, 'sqlite:///db.sqlite3'),

    STATIC_ROOT=(str, BASE_DIR / 'static'),
    MEDIA_ROOT=(str, BASE_DIR / 'media'),

    RECAPTCHA_SITE_KEY=(str, ''),
    RECAPTCHA_SECRET_KEY=(str, ''),

    TOC_ONLINE_BASE_URL=(str, 'https://api17.toconline.pt'),
    TOC_ONLINE_OAUTH_CLIENT_ID=(str, 'fake-client-id'),
    TOC_ONLINE_OAUTH_CLIENT_SECRET=(str, 'fake-client-secret'),
    TOC_ONLINE_OAUTH_REDIRECT_URI=(str, 'https://oauth.pstmn.io/v1/callback'),

    DEEPL_AUTH_KEY=(str, 'fake-auth-key'),

    STRIPE_PUBLIC_KEY=(str, 'STRIPE_PUBLIC_KEY'),
    STRIPE_SECRET_KEY=(str, 'STRIPE_SECRET_KEY'),
    STRIPE_WEBHOOK_SECRET=(str, 'STRIPE_WEBHOOK_SECRET'),
)


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG')

EMAIL_HOST = env('EMAIL_HOST')
EMAIL_PORT = env('EMAIL_PORT')
EMAIL_HOST_USER = env('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD')
EMAIL_USE_TLS = env('EMAIL_USE_TLS')
EMAIL_USE_SSL = env('EMAIL_USE_SSL')

DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL')
RECIPIENT_LIST_ON_CONTACT_US_REQUEST = env('RECIPIENT_LIST_ON_CONTACT_US_REQUEST')

ALLOWED_HOSTS = env('ALLOWED_HOSTS')

X_FRAME_OPTIONS = 'SAMEORIGIN'

XS_SHARING_ALLOWED_METHODS = ['HEAD', 'GET', 'OPTIONS',]

# Application definition

INSTALLED_APPS = [
    # 3rd party apps that need to be before Django apps
    'modeltranslation',
    
    # Django apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # 3rd party apps,
    'django_browser_reload',
    'django_dump_die',

    # local apps
    'accounts',
    'pets',
    'hotel',
    'backoffice',
    'frontoffice',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django_dump_die.middleware.DumpAndDieMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_browser_reload.middleware.BrowserReloadMiddleware',
]

ROOT_URLCONF = 'olhocao.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
            BASE_DIR / 'olhocao' / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'olhocao.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    'default': env.db()
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/logout/'


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGES = [
    ("en", _("English")),
    ("pt", _("Portuguese")),
]

LOCALE_PATHS = [
    BASE_DIR / 'locale'
]

LANGUAGE_CODE = 'en'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'

STATIC_ROOT = env('STATIC_ROOT')

STATICFILES_DIRS = [
    BASE_DIR / 'assets',
]


# Media files (Uploaded files)
# https://docs.djangoproject.com/en/5.0/ref/settings/#media-root
# https://docs.djangoproject.com/en/5.0/ref/settings/#media-url
MEDIA_ROOT = env('MEDIA_ROOT')

MEDIA_URL = 'media/'

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Fixtures Directories to lookup
# https://docs.djangoproject.com/en/5.0/ref/settings/#std-setting-FIXTURE_DIRS
FIXTURE_DIRS = [
    BASE_DIR / 'fixtures'
]

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


TOC_ONLINE_BASE_URL = env('TOC_ONLINE_BASE_URL')
TOC_ONLINE_OAUTH_CLIENT_ID = env('TOC_ONLINE_OAUTH_CLIENT_ID')
TOC_ONLINE_OAUTH_CLIENT_SECRET = env('TOC_ONLINE_OAUTH_CLIENT_SECRET')
TOC_ONLINE_OAUTH_REDIRECT_URI = env('TOC_ONLINE_OAUTH_REDIRECT_URI')


DEEPL_AUTH_KEY = env('DEEPL_AUTH_KEY')


STRIPE_PUBLIC_KEY = env('STRIPE_PUBLIC_KEY')
STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = env('STRIPE_WEBHOOK_SECRET')
