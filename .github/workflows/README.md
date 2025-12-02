# GitHub Actions Workflows

This directory contains GitHub Actions workflows for building and publishing the Discord Super Pal bot.

## Workflows

### 1. Manual Docker Build (`manual-build.yml`)

**Trigger:** Manual workflow dispatch

**Purpose:** Build Docker images on-demand without publishing to Docker Hub. Images are uploaded as GitHub Actions artifacts for testing and verification.

**Usage:**
1. Go to the **Actions** tab in GitHub
2. Select **"Manual Docker Build"** from the workflows list
3. Click **"Run workflow"**
4. Choose options:
   - **Build target**: Which image to build (super-pal, spin-the-wheel, or both)
   - **Platforms**: Architecture to build for (linux/amd64, linux/arm64, or both)
5. Click **"Run workflow"**

**Outputs:**
- Docker image(s) uploaded as artifacts (retained for 7 days)
- Build summary with image details in the workflow run summary

**Downloading artifacts:**
1. Navigate to the completed workflow run
2. Scroll to the bottom to the "Artifacts" section
3. Download the `.tar` file
4. Load the image locally:
   ```bash
   docker load --input super-pal-docker-image.tar
   # or
   docker load --input spin-the-wheel-docker-image.tar
   ```

### 2. Publish Docker Image (`docker-build-and-upload.yml`)

**Trigger:** Release published

**Purpose:** Automatically build and publish Docker images to Docker Hub when a new release is created.

**Usage:**
1. Create a new release on GitHub
2. The workflow automatically builds and pushes images to Docker Hub
3. Images are tagged with the release version (semver)

**Requirements:**
- `DOCKER_USERNAME` secret configured
- `DOCKER_PASSWORD` secret configured

## Image Information

### Super Pal Image
- **Dockerfile:** `Dockerfile.super-pal`
- **Base Image:** `python:3.13-slim-bookworm`
- **Docker Hub:** `adamhurm/discord-super-pal` (releases only)
- **Purpose:** Main Discord bot for Super Pal of the Week

### Spin The Wheel Image
- **Dockerfile:** `Dockerfile.spin-the-wheel`
- **Purpose:** Spin The Wheel integration bot

## Platform Support

Both workflows support multi-platform builds:
- **linux/amd64** - Standard x86_64 architecture
- **linux/arm64** - ARM64 architecture (e.g., Raspberry Pi 4, AWS Graviton)

## Artifact Retention

Manual build artifacts are retained for **7 days** by default. After this period, they are automatically deleted from GitHub Actions.

## Notes

- Manual builds do **not** push to Docker Hub
- Manual builds are useful for testing changes before creating a release
- Release builds require proper semantic versioning tags
- Multi-platform builds may take longer to complete
