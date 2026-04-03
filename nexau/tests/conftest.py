"""
Pytest configuration for NexAU integration tests.

This file provides fixtures and configuration for integration tests.
"""

import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def temp_config_file():
    """Fixture to create a temporary agent configuration file for testing."""
    config_content = """
name: test_agent
llm:
  model: test-model
  api_key: test-key
system_prompt: You are a test agent for integration testing.
"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(config_content)
        temp_file = f.name
    
    yield temp_file
    
    # Clean up
    if os.path.exists(temp_file):
        os.unlink(temp_file)


@pytest.fixture
def minimal_agent_config():
    """Fixture providing a minimal agent configuration dictionary."""
    return {
        "name": "test_agent",
        "llm": {
            "model": "test-model",
            "api_key": "test-key"
        },
        "system_prompt": "You are a test agent."
    }


@pytest.fixture
def project_root():
    """Fixture providing the project root directory."""
    return Path("/gpfs/users/panchengjun/NexAU/nexau")


def pytest_configure(config):
    """Pytest configuration hook."""
    # Add custom markers
    config.addinivalue_line("markers", "e2e: mark test as end-to-end (requires specific setup)")
    config.addinivalue_line("markers", "slow: mark test as slow running")


def pytest_addoption(parser):
    """Add custom command line options for integration tests."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run end-to-end tests that require specific setup"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection based on command line options."""
    if not config.getoption("--run-e2e"):
        # Skip e2e tests unless explicitly requested
        skip_e2e = pytest.mark.skip(reason="Need --run-e2e option to run")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)