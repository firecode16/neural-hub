from setuptools import setup, find_packages

setup(
    name="neural-hub",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "neural-protocol>=1.0.0",
    ],
    extras_require={
        "dashboard": ["aiohttp>=3.8.0"],   # dependencia opcional para el dashboard
    },
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "neural-hub = neural_hub.scripts.run_hub:main",
        ],
    },
    author="NeuralProtocol Team",
    description="Servidor central para NeuralProtocol (WebSocket hub) con dashboard opcional",
    license="MIT",
)