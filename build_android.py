#!/usr/bin/env python3
"""
Android APK build script for CLC Kenya Flet app.
Supports buildozer-based builds with customizable options.
"""

import os
import sys
import subprocess
import argparse
import json
from pathlib import Path
from datetime import datetime


class AndroidBuilder:
    """Handle Android APK building with buildozer."""
    
    def __init__(self, build_number=None, commit_id=None):
        self.build_number = build_number or os.environ.get('GITHUB_RUN_NUMBER', '1')
        self.commit_id = commit_id or os.environ.get('GITHUB_SHA', 'unknown')[:8]
        self.project_root = Path(__file__).parent
        self.build_dir = self.project_root / 'build'
        self.apk_dir = self.build_dir / 'apk'
        self.logs_dir = self.build_dir / 'logs'
        
        # Create directories
        self.apk_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
    
    def log(self, message, level='INFO'):
        """Log message with timestamp."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] [{level}] {message}")
    
    def run_command(self, cmd, description=None):
        """Execute shell command and log output."""
        description = description or ' '.join(cmd)
        self.log(f"Running: {description}")
        
        log_file = self.logs_dir / f"{description.replace(' ', '_')}.log"
        
        try:
            with open(log_file, 'w') as f:
                result = subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=True
                )
            self.log(f"✅ {description} completed")
            return result
        except subprocess.CalledProcessError as e:
            self.log(f"❌ {description} failed with code {e.returncode}", level='ERROR')
            self.log(f"See logs: {log_file}", level='ERROR')
            raise
        except Exception as e:
            self.log(f"❌ {description} error: {e}", level='ERROR')
            raise
    
    def check_prerequisites(self):
        """Check if all required tools are available."""
        self.log("Checking prerequisites...")
        
        tools = {
            'python': ['python', '--version'],
            'java': ['java', '-version'],
            'android': ['android', '--version']  # If using SDK tools
        }
        
        for tool, cmd in tools.items():
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                self.log(f"✅ {tool} available")
            except (subprocess.CalledProcessError, FileNotFoundError):
                self.log(f"⚠️  {tool} not found (may be OK depending on build system)", level='WARN')
    
    def setup_buildozer(self):
        """Setup buildozer configuration if needed."""
        self.log("Setting up buildozer...")
        
        buildozer_spec = self.project_root / 'buildozer.spec'
        
        if not buildozer_spec.exists():
            self.log("buildozer.spec not found, using default config")
            return
        
        # You could customize buildozer.spec here if needed
        self.log("✅ buildozer.spec ready")
    
    def build_apk(self):
        """Build the APK using buildozer or flet."""
        self.log("Building APK...")
        
        # Option 1: Using buildozer (if configured)
        buildozer_spec = self.project_root / 'buildozer.spec'
        if buildozer_spec.exists():
            self.run_command(
                ['buildozer', 'android', 'release'],
                description='buildozer APK build'
            )
        else:
            # Option 2: Using flet build (if available)
            self.log("Attempting flet build...")
            self.run_command(
                ['flet', 'build', 'apk', '--output', str(self.apk_dir / 'app.apk')],
                description='flet APK build'
            )
    
    def verify_apk(self):
        """Verify APK was built successfully."""
        self.log("Verifying APK...")
        
        apk_files = list(self.apk_dir.glob('*.apk'))
        
        if not apk_files:
            raise FileNotFoundError("No APK files found in build output")
        
        apk_file = apk_files[0]
        
        # Verify file size is reasonable (> 1MB)
        size = apk_file.stat().st_size
        if size < 1_000_000:
            self.log(f"⚠️  APK size suspiciously small: {size} bytes", level='WARN')
        
        self.log(f"✅ APK verified: {apk_file.name} ({size / 1_000_000:.1f}MB)")
        return apk_file
    
    def generate_metadata(self, apk_file):
        """Generate build metadata JSON."""
        self.log("Generating metadata...")
        
        metadata = {
            'build_number': self.build_number,
            'commit_id': self.commit_id,
            'apk_name': apk_file.name,
            'apk_size': apk_file.stat().st_size,
            'build_timestamp': datetime.now().isoformat(),
            'github_run_id': os.environ.get('GITHUB_RUN_ID', 'unknown'),
            'github_action': os.environ.get('GITHUB_ACTION', 'unknown'),
        }
        
        metadata_file = self.build_dir / 'metadata.json'
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        self.log(f"✅ Metadata saved to {metadata_file.name}")
        return metadata
    
    def build(self):
        """Execute full build process."""
        try:
            self.log(f"Starting Android APK build (build#{self.build_number}, commit:{self.commit_id})")
            self.check_prerequisites()
            self.setup_buildozer()
            self.build_apk()
            apk_file = self.verify_apk()
            metadata = self.generate_metadata(apk_file)
            
            self.log("=" * 60)
            self.log("🎉 Build completed successfully!")
            self.log("=" * 60)
            self.log(f"APK: {metadata['apk_name']}")
            self.log(f"Size: {metadata['apk_size'] / 1_000_000:.1f}MB")
            self.log(f"Build #: {metadata['build_number']}")
            self.log(f"Commit: {metadata['commit_id']}")
            self.log("=" * 60)
            
            return 0
        
        except Exception as e:
            self.log(f"Build failed: {e}", level='ERROR')
            return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Build Android APK for CLC Kenya Flet app'
    )
    parser.add_argument('--build-number', help='Build number (default: GITHUB_RUN_NUMBER or 1)')
    parser.add_argument('--commit-id', help='Commit ID (default: GITHUB_SHA[:8] or unknown)')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    builder = AndroidBuilder(
        build_number=args.build_number,
        commit_id=args.commit_id
    )
    
    return builder.build()


if __name__ == '__main__':
    sys.exit(main())
