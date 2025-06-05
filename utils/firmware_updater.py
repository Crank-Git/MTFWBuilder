"""
Meshtastic Firmware Updater

This module handles downloading, extracting, and setting up the latest Meshtastic
firmware from GitHub releases. It provides functionality to keep the firmware
source code up-to-date for on-demand firmware building.

The updater integrates with the GitHub API to fetch the latest releases and
uses PlatformIO to set up the build environment for custom firmware compilation.

Author: Meshtastic Configuration Generator Contributors
License: MIT
"""

import os
import shutil
import subprocess
import tempfile
import zipfile
import requests
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("firmware_updater.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("firmware_updater")

def get_latest_release_info() -> Optional[Dict[str, Any]]:
    """
    Get information about the latest Meshtastic firmware release from GitHub.
    
    Fetches release data from the GitHub API including version information,
    download URLs, and asset details.
    
    Returns:
        Optional[Dict[str, Any]]: Dictionary containing release information
                                 including version, published date, and assets.
                                 Returns None if the API request fails.
                                 
    Example:
        >>> release_info = get_latest_release_info()
        >>> if release_info:
        ...     print(f"Latest version: {release_info['version']}")
    """
    try:
        logger.info("Fetching latest release info from GitHub...")
        api_url = "https://api.github.com/repos/meshtastic/firmware/releases/latest"
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        
        release_data = response.json()
        version = release_data['tag_name']
        published_date = release_data['published_at']
        
        logger.info(f"Latest release: {version} (published: {published_date})")
        return {
            'version': version,
            'published_date': published_date,
            'assets': release_data['assets'],
            'release_data': release_data
        }
    except Exception as e:
        logger.error(f"Error fetching release info: {str(e)}")
        return None

def download_source_code(release_info: Dict[str, Any], target_dir: str) -> Optional[str]:
    """
    Download the source code ZIP from the GitHub release.
    
    Args:
        release_info: Dictionary containing release information from GitHub API
        target_dir: Directory where the ZIP file should be saved
        
    Returns:
        Optional[str]: Path to the downloaded ZIP file, or None if download fails
    """
    try:
        source_url = release_info['release_data']['zipball_url']
        logger.info(f"Downloading source code from {source_url}...")
        
        zip_path = os.path.join(target_dir, "firmware_source.zip")
        response = requests.get(source_url, stream=True, timeout=60)
        response.raise_for_status()
        
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        logger.info(f"Source code downloaded to {zip_path}")
        return zip_path
    except Exception as e:
        logger.error(f"Error downloading source code: {str(e)}")
        return None

def extract_firmware(zip_path: str, extract_dir: str) -> Optional[str]:
    """
    Extract the firmware source code from the downloaded ZIP file.
    
    Args:
        zip_path: Path to the firmware source ZIP file
        extract_dir: Directory where the source should be extracted
        
    Returns:
        Optional[str]: Path to the extracted firmware directory, or None if extraction fails
    """
    try:
        logger.info(f"Extracting firmware to {extract_dir}...")
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Get the top-level directory name in the ZIP
            top_dir = zip_ref.namelist()[0].split('/')[0]
            zip_ref.extractall(extract_dir)
        
        # Return the path to the extracted firmware directory
        firmware_dir = os.path.join(extract_dir, top_dir)
        logger.info(f"Firmware extracted to {firmware_dir}")
        return firmware_dir
    except Exception as e:
        logger.error(f"Error extracting firmware: {str(e)}")
        return None

def setup_platformio_environment(firmware_dir: str) -> bool:
    """
    Set up the PlatformIO environment for building the firmware.
    
    Initializes PlatformIO and installs all required dependencies for
    building Meshtastic firmware.
    
    Args:
        firmware_dir: Path to the firmware source directory
        
    Returns:
        bool: True if setup was successful, False otherwise
    """
    try:
        logger.info("Setting up PlatformIO environment...")
        
        # Run PlatformIO commands to initialize and set up the environment
        cmd = f"cd {firmware_dir} && pio pkg install"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"Error setting up PlatformIO: {result.stderr}")
            return False
        
        logger.info("PlatformIO environment set up successfully")
        return True
    except Exception as e:
        logger.error(f"Error setting up PlatformIO environment: {str(e)}")
        return False

def prebuild_common_variants(firmware_dir: str, prebuilt_dir: str) -> bool:
    """
    Pre-build the most common firmware variants to speed up future builds.
    
    Compiles popular device variants ahead of time so that users don't have
    to wait for full compilation when building custom firmware.
    
    Args:
        firmware_dir: Path to the firmware source directory
        prebuilt_dir: Directory where pre-built firmware files should be stored
        
    Returns:
        bool: True if pre-building was successful, False otherwise
    """
    try:
        logger.info("Pre-building common firmware variants...")
        os.makedirs(prebuilt_dir, exist_ok=True)
        
        # List of common variants to pre-build
        common_variants: List[str] = [
            "tbeam", 
            "heltec-v2.1",
            "tlora-v2",
            "tlora-v2-1-1.6",
            "tbeam-s3-core",
            "tracker-t1000-e"
        ]
        
        for variant in common_variants:
            logger.info(f"Pre-building {variant}...")
            cmd = f"cd {firmware_dir} && pio run -e {variant} --jobs 4"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.warning(f"Error pre-building {variant}: {result.stderr}")
                continue
            
            # Copy the built firmware to our prebuilt directory
            source_firmware = os.path.join(firmware_dir, ".pio", "build", variant, "firmware.uf2")
            if os.path.exists(source_firmware):
                target_path = os.path.join(prebuilt_dir, f"{variant}_base.uf2")
                shutil.copy(source_firmware, target_path)
                logger.info(f"Pre-built {variant} saved to {target_path}")
        
        logger.info("Pre-building completed")
        return True
    except Exception as e:
        logger.error(f"Error pre-building variants: {str(e)}")
        return False

def update_firmware() -> bool:
    """
    Main function to update the firmware to the latest version.
    
    This is the primary entry point for the firmware update process. It:
    1. Downloads the latest firmware source from GitHub
    2. Extracts and sets up the build environment
    3. Updates version tracking information
    
    Returns:
        bool: True if the update was successful, False otherwise
        
    Example:
        >>> if update_firmware():
        ...     print("Firmware updated successfully!")
        ... else:
        ...     print("Firmware update failed")
    """
    logger.info("Starting firmware update process...")
    
    # Create temporary directory for downloading and extracting
    temp_dir = tempfile.mkdtemp()
    
    try:
        # Get the app's base directory
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        firmware_dest_dir = os.path.join(base_dir, "firmware")
        
        # Get latest release info
        release_info = get_latest_release_info()
        if not release_info:
            logger.error("Failed to get release info. Aborting.")
            return False
        
        # Save version info for future reference
        version_file = os.path.join(base_dir, "firmware_version.txt")
        with open(version_file, "w") as f:
            f.write(f"Version: {release_info['version']}\n")
            f.write(f"Updated: {datetime.now().isoformat()}\n")
        
        # Download firmware source
        zip_path = download_source_code(release_info, temp_dir)
        if not zip_path:
            logger.error("Failed to download source code. Aborting.")
            return False
        
        # Extract firmware
        extracted_dir = extract_firmware(zip_path, temp_dir)
        if not extracted_dir:
            logger.error("Failed to extract firmware. Aborting.")
            return False
        
        # Remove existing firmware directory if it exists
        if os.path.exists(firmware_dest_dir):
            logger.info(f"Removing existing firmware directory: {firmware_dest_dir}")
            shutil.rmtree(firmware_dest_dir)
        
        # Move extracted firmware to destination
        logger.info(f"Moving firmware to {firmware_dest_dir}")
        shutil.move(extracted_dir, firmware_dest_dir)
        
        # Set up PlatformIO
        if not setup_platformio_environment(firmware_dest_dir):
            logger.error("Failed to set up PlatformIO environment. Aborting.")
            return False
        
        # Note: We build firmware on-demand rather than pre-building variants
        # This saves disk space and ensures fresh builds with user configurations
        
        logger.info("Firmware update completed successfully!")
        return True
    
    except Exception as e:
        logger.error(f"Unexpected error during firmware update: {str(e)}")
        return False
    
    finally:
        # Clean up temporary directory
        logger.info(f"Cleaning up temporary directory: {temp_dir}")
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    update_firmware() 