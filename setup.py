#!/usr/bin/env python3

from setuptools import setup, find_packages

requires = [
    'requests',
]

setup(
    name='lcoreapi',
    version='1.0',
    description='lambdacore client api',
    packages=find_packages(),
    include_package_data=True,
    test_suite='lcoreapi',
    install_requires=requires,
)

