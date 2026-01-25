#!/usr/bin/env bash

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
print_step() {
    echo -e "${BLUE}==>${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Banner
echo -e "${BLUE}"
cat << "EOF"
╔═══════════════════════════════════════════╗
║   Python Repository Template Setup       ║
╚═══════════════════════════════════════════╝
EOF
echo -e "${NC}"

# Check if we're in the template directory
if [ ! -f "pyproject.toml" ] || [ ! -f "mkdocs.yaml" ]; then
    print_error "This script must be run from the template root directory"
    exit 1
fi

# Get project information
print_step "Project Configuration"
echo ""

# Project name
read -p "Enter project name (e.g., my-awesome-project): " project_name
if [ -z "$project_name" ]; then
    print_error "Project name is required"
    exit 1
fi

# Convert project name to valid Python package name (replace hyphens with underscores)
package_name=$(echo "$project_name" | tr '-' '_' | tr '[:upper:]' '[:lower:]')

# Project description
read -p "Enter project description: " project_description
if [ -z "$project_description" ]; then
    project_description="A Python project"
fi

# Site name for documentation (default to title case of project name)
default_site_name=$(echo "$project_name" | sed 's/-/ /g' | sed 's/\b\(.\)/\u\1/g')
read -p "Enter documentation site name [$default_site_name]: " site_name
site_name=${site_name:-$default_site_name}

# Git configuration
echo ""
print_step "Git Configuration"
echo ""

read -p "Reinitialize git repository? This will remove the template's git history (y/n) [y]: " reinit_git
reinit_git=${reinit_git:-y}

if [ "$reinit_git" = "y" ]; then
    read -p "Enter git remote URL (optional, press enter to skip): " git_remote
fi

# Confirm settings
echo ""
print_step "Configuration Summary"
echo ""
echo "  Project name:        $project_name"
echo "  Package name:        $package_name"
echo "  Description:         $project_description"
echo "  Documentation name:  $site_name"
echo "  Reinitialize git:    $reinit_git"
if [ -n "$git_remote" ]; then
    echo "  Git remote:          $git_remote"
fi
echo ""

read -p "Proceed with setup? (y/n) [y]: " confirm
confirm=${confirm:-y}

if [ "$confirm" != "y" ]; then
    print_warning "Setup cancelled"
    exit 0
fi

echo ""

# Update pyproject.toml
print_step "Updating pyproject.toml"
if command -v python3 &> /dev/null; then
    python3 << EOF
import re

with open('pyproject.toml', 'r') as f:
    content = f.read()

# Update name
content = re.sub(r'name = ".*?"', 'name = "$project_name"', content)

# Update description
content = re.sub(r'description = ".*?"', 'description = "$project_description"', content)

with open('pyproject.toml', 'w') as f:
    f.write(content)
EOF
    print_success "Updated pyproject.toml"
else
    # Fallback to sed if Python is not available
    sed -i.bak "s/name = \".*\"/name = \"$project_name\"/" pyproject.toml
    sed -i.bak "s/description = \".*\"/description = \"$project_description\"/" pyproject.toml
    rm -f pyproject.toml.bak
    print_success "Updated pyproject.toml"
fi

# Update mkdocs.yaml
print_step "Updating mkdocs.yaml"
if command -v python3 &> /dev/null; then
    python3 << EOF
import re

with open('mkdocs.yaml', 'r') as f:
    content = f.read()

# Update site_name
content = re.sub(r'site_name: .*', 'site_name: $site_name', content)

with open('mkdocs.yaml', 'w') as f:
    f.write(content)
EOF
    print_success "Updated mkdocs.yaml"
else
    sed -i.bak "s/site_name: .*/site_name: $site_name/" mkdocs.yaml
    rm -f mkdocs.yaml.bak
    print_success "Updated mkdocs.yaml"
fi

# Rename src directory if package name is different
if [ "$package_name" != "your_project_name" ] && [ -d "src" ]; then
    print_step "Creating package directory structure"
    # Create the new package directory inside src
    mkdir -p "src/$package_name"

    # Move __init__.py to the new package directory
    if [ -f "src/__init__.py" ]; then
        mv "src/__init__.py" "src/$package_name/__init__.py"
    fi

    print_success "Created src/$package_name package"
fi

# Git operations
if [ "$reinit_git" = "y" ]; then
    print_step "Reinitializing git repository"

    # Remove existing git history
    rm -rf .git

    # Initialize new repository
    git init
    print_success "Initialized new git repository"

    # Add remote if provided
    if [ -n "$git_remote" ]; then
        git remote add origin "$git_remote"
        print_success "Added remote: $git_remote"
    fi

    # Create initial commit
    git add .
    git commit -m "Initial commit: $project_name"
    print_success "Created initial commit"
fi

# Check for uv installation
print_step "Checking for uv package manager"
if ! command -v uv &> /dev/null; then
    print_warning "uv is not installed"
    echo "  Install uv with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    read -p "Would you like to install uv now? (y/n) [y]: " install_uv
    install_uv=${install_uv:-y}

    if [ "$install_uv" = "y" ]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # Source the shell config to make uv available
        export PATH="$HOME/.local/bin:$PATH"
        print_success "Installed uv"
    else
        print_warning "Skipping dependency installation (uv required)"
    fi
fi

# Install dependencies
if command -v uv &> /dev/null; then
    print_step "Installing dependencies"
    uv sync --dev
    print_success "Installed dependencies"

    # Install pre-commit hooks
    print_step "Installing pre-commit hooks"
    uv run pre-commit install
    print_success "Installed pre-commit hooks"
else
    print_warning "Skipped dependency installation (uv not available)"
fi

# Delete setup script
print_step "Cleaning up"
read -p "Delete this setup script? (y/n) [y]: " delete_script
delete_script=${delete_script:-y}

if [ "$delete_script" = "y" ]; then
    rm -f setup.sh
    print_success "Deleted setup.sh"
fi

# Success message
echo ""
echo -e "${GREEN}"
cat << "EOF"
╔═══════════════════════════════════════════╗
║         Setup Complete! 🎉                ║
╚═══════════════════════════════════════════╝
EOF
echo -e "${NC}"

echo "Your project is ready to use!"
echo ""
echo "Next steps:"
echo "  1. Edit src/$package_name/__init__.py to add your code"
echo "  2. Update docs/index.md with your project documentation"
echo "  3. Run tests: uv run pytest"
echo "  4. Build docs: uv run mkdocs serve"
if [ -n "$git_remote" ]; then
    echo "  5. Push to remote: git push -u origin master"
fi
echo ""
print_success "Happy coding!"
