#!/usr/bin/env python
"""
Package metadata for application.
"""
import os
import re
import sys
from setuptools import find_packages, setup




def get_version(*file_paths):
    """
    Extract the version string from the file.
    """
    filename = os.path.join(os.path.dirname(__file__), *file_paths)
    version_file = open(filename, encoding="utf8").read()
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]",
                              version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError('Unable to find version string.')


def load_requirements(*requirements_paths):
    """
    Load all requirements from the specified requirements files.
    """
    requirements = []
    for path in requirements_paths:
        with open(path) as reqs:
            for line in reqs:
                line = line.strip()
                if line and not line.startswith(("-", "#")):
                    requirements.append(line)
    return requirements


VERSION = get_version('application', '__init__.py')

if sys.argv[-1] == 'tag':
    print("Tagging the version on github:")
    os.system(f"git tag -a {VERSION} -m 'version {VERSION}'")
    os.system("git push --tags")
    sys.exit()

with open(os.path.join(os.path.dirname(__file__), 'README.rst'), encoding="utf8") as f:
    README = f.read()

with open(os.path.join(os.path.dirname(__file__), 'CHANGELOG.rst'), encoding="utf8") as f:
    CHANGELOG = f.read()

setup(
    name='application',
    version=VERSION,
    description="One-line description for README and other doc files.",
    long_description=README + '\n\n' + CHANGELOG,
    author='Open edX Project',
    author_email='oscm@openedx.org',
    url='https://github.com/application/application',
    packages=find_packages(
        include=['application', 'application.*'],
        exclude=["*tests"],
    ),
    include_package_data=True,
    install_requires=load_requirements('requirements/base.in'),
    python_requires=">=3.11",
    license="Apache Software License 2.0",
    zip_safe=False,
    keywords='Python edx',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Framework :: Django',
        'Framework :: Django :: 4.2',
        'Framework :: Django :: 5.2',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.12',
    ],

    entry_points={
        "lms.djangoapp": [
            "application = application.apps:ApplicationConfig",
        ],
    },
)
