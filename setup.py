from setuptools import setup

setup(
    name='homu',
    version='0.3.0',
    author='Barosl Lee',
    url='https://github.com/barosl/homu',
    test_suite='homu.tests',
    description=('A bot that integrates with GitHub '
                 'and your favorite continuous integration service'),

    packages=['homu'],
    install_requires=[
        'github3.py==0.9.6',
        'toml',
        'Jinja2',
        'requests',
        'bottle',
        'waitress',
        'retrying',
    ],
    setup_requires=[
        'pytest-runner',
    ],
    tests_require=[
        'pytest',
    ],
    package_data={
        'homu': [
            'html/*.html',
            'assets/*',
        ],
    },
    entry_points={
        'console_scripts': [
            'homu=homu.main:main',
        ],
    },
    zip_safe=False,
)
