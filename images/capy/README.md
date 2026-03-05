# Capy Linux Images

Pre-built Docker images for capy's Linux CI matrix.

This directory provides:

- 2 shared base images
- 8 project images (matrix variants)
- build and validation scripts

## Image Set

### Base images

- `capy-ubuntu-25.04-base`
- `capy-ubuntu-24.04-base`

### Project images

- `capy-ubuntu-25.04-gcc15`
- `capy-ubuntu-25.04-gcc15-asan`
- `capy-ubuntu-24.04-gcc13-cov`
- `capy-ubuntu-24.04-clang17`
- `capy-ubuntu-24.04-clang20`
- `capy-ubuntu-24.04-clang20-asan`
- `capy-ubuntu-24.04-clang20-x86`
- `capy-ubuntu-22.04-gcc12`

## Scripts

- `build-all.sh`: build all images in dependency order (`--save` optional)
- `build-one.sh`: build one image by name (`--save` optional)
- `test-image.sh`: validate toolchain and variant-specific expectations

## Notes

- Boost source is pre-cloned at `/opt/boost-source`.
- `b2` is installed to `/usr/local/bin/b2`.
- `ccache` is configured with `CCACHE_DIR=/cache/ccache`.
