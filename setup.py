from setuptools import find_packages, setup

setup(
    name='monplat',
    version='0.1.0',
    description='Zabbix 2.4 monitoring toolkit: provisioning, collection, '
                'Grafana bridge, forecasting and remediation',
    packages=find_packages(exclude=['tests', 'tests.*']),
    entry_points={
        'console_scripts': [
            'mpctl = monplat.cli:main',
        ],
    },
)
