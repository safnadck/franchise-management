"""
These settings are here to use during tests, because django requires them.

In a real-world use case, apps in this project are installed into other
Django applications, so these settings will not be used.
"""

from os.path import abspath, dirname, join
import os


def root(*args):
    """
    Get the absolute path of the given path relative to the project root.
    """
    return join(abspath(dirname(__file__)), *args)

def plugin_settings(settings):
    print("✔ Application settings loaded!")
    settings.FEATURES['ENABLE_APPLICATION'] = True

    
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': 'default.db',
        'USER': '',
        'PASSWORD': '',
        'HOST': '',
        'PORT': '',
    }
}

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.messages',
    'django.contrib.sessions',
    'application',
)

LOCALE_PATHS = [
    root('application', 'conf', 'locale'),
]


DEBUG = True

# Email settings are handled by the main Open edX application
# Do not override here to ensure compatibility with production email setup


ALLOWED_HOSTS = []


ROOT_URLCONF = 'application.urls'

SECRET_KEY = 'insecure-secret-key'

MIDDLEWARE = (
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'application', 'static'),
]


# -------------------
# EMAIL CONFIGURATION
# -------------------
