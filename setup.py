from setuptools import find_packages, setup

setup(
    name="actual",
    version="0.0.1",
    packages=find_packages(),
    description="Implementation of the Actual API to interact with Actual over Python.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Brunno Vanelli",
    author_email="brunnovanelli@gmail.com",
    url="https://github.com/bvanelli/actualpy",
    zip_safe=False,
    project_urls={
        "Issues": "https://github.com/bvanelli/actualpy/issues",
    },
)
