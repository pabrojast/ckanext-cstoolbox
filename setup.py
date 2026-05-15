# -*- coding: utf-8 -*-
from setuptools import setup, find_namespace_packages

setup(
    name="ckanext-cstoolbox",
    version="0.1.0",
    description=(
        "CKAN extension for UNESCO Citizen Science Toolbox (CST) — "
        "curated Quartex survey views with interactive dashboards and GeoJSON export."
    ),
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="PabloRojast",
    url="https://github.com/pabrojast/ckanext-cstoolbox",
    license="AGPL-3.0",
    packages=find_namespace_packages(include=["ckanext.*"]),
    include_package_data=True,
    package_data={
        "ckanext.cstoolbox": [
            "templates/**/*.html",
            "public/**/*",
            "logic/*.py",
        ],
    },
    python_requires=">=3.8",
    install_requires=[],
    entry_points={
        "ckan.plugins": [
            "cstoolbox = ckanext.cstoolbox.plugin:CSToolboxPlugin",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Framework :: CKAN",
        "License :: OSI Approved :: GNU Affero General Public License v3",
        "Programming Language :: Python :: 3",
    ],
)
