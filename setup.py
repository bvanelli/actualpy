from setuptools import find_packages, setup

__version__ = ""  # avoid linting issues on the file, but the line below will fill in the version
exec(open("actual/version.py").read())
setup(
    name="actualpy",
    version=__version__,
    packages=find_packages(),
    description="Implementation of the Actual API to interact with Actual over Python.",
    long_description=open("README.md").read().replace("> [!WARNING]", "⚠️**Warning**: "),
    long_description_content_type="text/markdown",
    author="Brunno Vanelli",
    author_email="brunnovanelli@gmail.com",
    url="https://github.com/bvanelli/actualpy",
    zip_safe=False,
    project_urls={
        "Issues": "https://github.com/bvanelli/actualpy/issues",
    },
    install_requires=["cryptography", "proto-plus", "python-dateutil", "requests", "sqlmodel"],
    extras_require={
        "cli": ["rich", "typer", "pyyaml"],
    },
    entry_points="""
      [console_scripts]
      actualpy=actual.cli.main:app
      """,
)
