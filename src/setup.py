from setuptools import find_packages, setup


setup(
    name="kicad2fritzing",
    version="0.1.0",
    description="Generate Fritzing-compatible parts from KiCad board data",
    packages=find_packages(),
    include_package_data=True,
    extras_require={
        "dev": [
            "pytest>=8.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "kicad2fritzing=kicad2fritzing.cli:main",
        ]
    },
)
