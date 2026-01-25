# Python Repository Template

A template repository for quickly setting up new Python projects with best practices and tooling.

## Features

This template includes:

- **Automated Setup**: Interactive bash script to configure your new project
- **VSCode Integration**: Pre-configured settings and recommended extensions
- **Code Quality**: Ruff for linting and formatting
- **Git Hooks**: Pre-commit hooks for automated code quality checks
- **Documentation**: MkDocs with Material theme and mkdocstrings
- **Testing**: Pytest configuration with coverage
- **Python 3.12+**: Modern Python with type hints support

## Quick Start

### Automated Setup (Recommended)

The easiest way to set up a new project from this template:

```bash
# Clone or copy this template
git clone <template-url> my-new-project
# OR: cp -r /path/to/use-repo-template my-new-project

# Navigate to the project directory
cd my-new-project

# Run the setup script
./setup.sh
```

The setup script will interactively:
- Prompt for your project name and description
- Update all configuration files (`pyproject.toml`, `mkdocs.yaml`)
- Create the package directory structure
- Optionally reinitialize the git repository
- Optionally set up a git remote
- Install dependencies using `uv`
- Set up pre-commit hooks
- Clean up the setup script itself

### Manual Setup

If you prefer to set up the project manually:

1. **Create a new repository from this template**
   ```bash
   # If using this as a template directory
   cp -r /path/to/use-repo-template /path/to/your-new-project
   cd /path/to/your-new-project
   ```

2. **Update project metadata**
   Edit `pyproject.toml` and update:
   - `name`: Your project name
   - `description`: Brief description
   - `dependencies`: Add your dependencies

3. **Update documentation**
   - Edit `README.md` (this file)
   - Update `mkdocs.yaml` with your site name
   - Create docs in `docs/` directory

4. **Rename the package directory**
   ```bash
   # Create your package directory
   mkdir -p src/your_package_name
   mv src/__init__.py src/your_package_name/__init__.py
   ```

5. **Initialize git (if not already done)**
   ```bash
   # Remove template's git history
   rm -rf .git

   # Initialize new repository
   git init
   git add .
   git commit -m "Initial commit from template"

   # Add remote (optional)
   git remote add origin <your-repo-url>
   ```

6. **Set up development environment**
   ```bash
   # Install uv if not already installed
   curl -LsSf https://astral.sh/uv/install.sh | sh

   # Create virtual environment and install dependencies
   uv sync --dev

   # Install pre-commit hooks
   uv run pre-commit install
   ```

## Project Structure

```
.
├── .vscode/              # VSCode settings and extensions
├── docs/                 # Documentation source files
├── src/                  # Source code
├── tests/                # Test files
├── .gitignore           # Git ignore patterns
├── .pre-commit-config.yaml  # Pre-commit hooks configuration
├── mkdocs.yaml          # MkDocs configuration
├── pyproject.toml       # Project configuration and dependencies
└── README.md            # This file
```

## Development

### Running Tests

```bash
uv run pytest
```

With coverage:
```bash
uv run pytest --cov=src --cov-report=html
```

### Code Quality

The project uses Ruff for linting and formatting:

```bash
# Check for issues
uv run ruff check .

# Auto-fix issues
uv run ruff check --fix .

# Format code
uv run ruff format .
```

Pre-commit hooks will automatically run these checks before each commit.

### Building Documentation

```bash
# Serve documentation locally
uv run mkdocs serve

# Build documentation
uv run mkdocs build
```

## Configuration

### Ruff

Ruff is configured in `pyproject.toml` with:
- Line length: 88 characters
- Google-style docstrings
- Import sorting
- Common Python linting rules

### Pre-commit

Pre-commit hooks are configured in `.pre-commit-config.yaml` and include:
- YAML validation
- File formatting (trailing whitespace, end of file)
- Large file detection
- Python syntax validation
- Ruff linting and formatting

### VSCode

VSCode settings in `.vscode/settings.json` include:
- Format on save with Ruff
- Organize imports on save
- Visual rulers at 72 and 88 characters
- Word wrap at 88 characters

## License

[Add your license here]

## Contributing

[Add contributing guidelines here]
