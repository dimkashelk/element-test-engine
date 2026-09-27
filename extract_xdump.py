#!/usr/bin/env python3
"""Standalone compatibility entry point for the shared xdump converter."""
import sys

from element_test.xdump import archive_path, extract_project, main, write_tar


if __name__ == "__main__":
    sys.exit(main())
