"""l2_p99_monitor 包安装配置"""

from setuptools import setup, find_packages

setup(
    name="l2_p99_monitor",
    version="1.0.0",
    description="通用 P99 监控告警包：解析性能日志 + 阈值检查 + 多渠道告警",
    long_description=open("README.md", encoding="utf-8").read() if __import__("os").path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    author="Yunshu Team",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[],  # 仅使用标准库，无外部依赖
    entry_points={
        "console_scripts": [
            "l2-p99-monitor=l2_p99_monitor.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Testing",
        "Topic :: System :: Monitoring",
    ],
)
