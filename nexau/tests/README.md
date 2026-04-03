# NexAU Integration Tests

This directory contains comprehensive integration tests for the NexAU tool functionality.

## Test Categories

### 1. Package Installation and Importability
- Tests that the package can be imported successfully
- Verifies core components are available
- Checks CLI wrapper functionality

### 2. Core Component Integration
- Agent configuration and validation
- Tool system integration
- Message system functionality
- Error handling mechanisms

### 3. CLI Functionality
- CLI wrapper availability
- Agent runner basic functionality
- Error handling scenarios

### 4. End-to-End Testing
- Full agent execution tests (requires specific setup)
- Configuration loading and validation

## Running Tests

### Basic Test Run
```bash
cd /path/to/nexau
python -m pytest tests/test_integration.py -v
```

### Using the Test Runner Script
```bash
# Run all tests
python tests/run_integration_tests.py

# Run with verbose output
python tests/run_integration_tests.py -v

# List available tests
python tests/run_integration_tests.py --list

# Run specific test pattern
python tests/run_integration_tests.py -k "test_package"
```

### End-to-End Tests
End-to-end tests require specific setup (LLM API keys, etc.) and are skipped by default:

```bash
# Enable end-to-end tests
RUN_E2E_TESTS=1 python -m pytest tests/test_integration.py -v

# Or use the script
python tests/run_integration_tests.py --e2e
```

## Test Configuration

The `conftest.py` file provides:
- Fixtures for temporary configuration files
- Custom pytest markers
- Configuration for test environment

## Test Coverage

The integration tests verify:

1. **Installation Verification**: Package can be imported and core components are available
2. **CLI Tool Testing**: Command-line interface wrapper functions correctly
3. **Configuration Validation**: Agent configurations are properly validated
4. **Component Integration**: All major components work together
5. **Error Handling**: Proper error handling and reporting
6. **Public API**: All public API components can be imported

## Dependencies

- pytest
- unittest.mock
- tempfile
- subprocess

## Notes

- Some tests may print informational messages about expected behavior variations
- End-to-end tests are skipped by default due to external dependencies
- The tests are designed to be robust and handle different environment configurations