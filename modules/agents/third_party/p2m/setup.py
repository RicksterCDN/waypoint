from setuptools import find_packages, setup

setup(
    name="p2m-policy",
    version="0.1.0",
    description="YAML-driven safety evaluation pipeline with LiteLLM-backed stages",
    packages=find_packages(include=["p2m*", "examples*"]),
    include_package_data=True,
    package_data={"p2m": ["prompts/*.md"]},
    install_requires=[
        "click>=8.0",
        "litellm>=1.79.1",
        "matplotlib>=3.10.8",
        "pydantic>=2.0",
        "python-dotenv>=1.2.2",
        "PyYAML>=6.0",
        "rapidfuzz>=3.9.0",
        "rich>=13.7.0",
    ],
)
