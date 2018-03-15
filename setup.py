from setuptools import setup

setup(
    name='homu',
    version='0.3.0',
    author='Barosl Lee',
    url='https://github.com/barosl/homu',
    description=('A bot that integrates with GitHub '
                 'and your favorite continuous integration service'),

    packages=['homu'],
    install_requires=[
        'github3.py<1.0',
        'toml',
        'Jinja2',
        'requests',
        'bottle',
        'waitress',
        'retrying',
    ],
    package_data={
        'homu': [
            'html/*.html',
        ],
    },
    entry_points={
        'console_scripts': [
            'homu=homu.main:main',
        ],
    },
    zip_safe=False,
)
