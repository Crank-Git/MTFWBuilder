"""
Meshtastic Configuration Generator

A Flask web application for generating Meshtastic device configurations
and building custom firmware with PlatformIO integration.

Author: Meshtastic Configuration Generator Contributors
License: MIT
"""

from flask import Flask, render_template, request, jsonify, send_file, make_response
from utils.jsonc_generator import generate_jsonc
import os
import tempfile
import shutil
import time
import subprocess
from utils.firmware_updater import update_firmware
import json
from typing import Dict, Any, Tuple, Optional

app = Flask(__name__)

# Create a dedicated directory for temporary files
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'meshtastic_config')
os.makedirs(TEMP_DIR, exist_ok=True)

def load_admin_config():
    """Load admin configuration from config.json"""
    config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'config.json')
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            return config.get('admin_password', 'meshtastic')  # fallback to default
    except (FileNotFoundError, json.JSONDecodeError):
        print("Warning: config.json not found or invalid, using default admin password")
        return 'meshtastic'  # fallback to default

# Load admin password at startup
ADMIN_PASSWORD = load_admin_config()

@app.route('/')
def index() -> str:
    """
    Render the main configuration generator page.
    
    Returns:
        str: Rendered HTML template for the main page
    """
    return render_template('index.html')

@app.route('/generate', methods=['POST'])
def generate() -> Tuple[Dict[str, Any], int] or str:
    """
    Generate JSONC configuration from form data.
    
    Returns:
        JSON response with configuration content or error message
    """
    try:
        # Get form data from request
        form_data = request.json
        
        # Generate JSONC content
        jsonc_content = generate_jsonc(form_data)
        
        # For preview, just return the content
        if form_data.get('preview_only', False):
            return jsonify({'success': True, 'content': jsonc_content})
        
        # Otherwise, create a file and return it directly
        return make_response(jsonc_content)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/preview', methods=['POST'])
def preview() -> Tuple[Dict[str, Any], int]:
    """
    Generate a preview of the JSONC configuration.
    
    Returns:
        JSON response with preview content or error message
    """
    try:
        # Get form data from request
        form_data = request.json
        
        # Debug: Print what we received
        print("Preview received:", form_data)
        
        # Generate JSONC content
        jsonc_content = generate_jsonc(form_data)
        
        # Debug: Print what we're returning
        print("Preview content:", jsonc_content)
        
        # Return the content for preview
        return jsonify({'success': True, 'content': jsonc_content})
    except Exception as e:
        print("Preview error:", str(e))
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download', methods=['POST'])
def download():
    """
    Download the generated JSONC configuration file.
    
    Returns:
        File download response or error JSON
    """
    try:
        # Get form data from request
        if request.is_json:
            form_data = request.json
        else:
            # Handle form submission with hidden input
            form_data = json.loads(request.form.get('config', '{}'))
        
        # Generate JSONC content
        jsonc_content = generate_jsonc(form_data)
        
        # Create a response with the content
        response = make_response(jsonc_content)
        response.headers["Content-Disposition"] = "attachment; filename=userPrefs.jsonc"
        response.headers["Content-Type"] = "application/json"
        return response
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/firmware-builder')
def firmware_builder() -> str:
    """
    Render the firmware builder page with supported device variants.
    
    Returns:
        str: Rendered HTML template for firmware builder
    """
    # Define all supported device variants organized by manufacturer
    variants = [
        # T-Beam Devices
        {"id": "tbeam", "name": "LILYGO® T-Beam", "manufacturer": "LILYGO"},
        {"id": "tbeam-s3-core", "name": "LILYGO® T-Beam S3 Core", "manufacturer": "LILYGO"},
        {"id": "tbeam0_7", "name": "LILYGO® T-Beam v0.7", "manufacturer": "LILYGO"},
        
        # T-Echo Devices
        {"id": "t-echo", "name": "LILYGO® T-Echo", "manufacturer": "LILYGO"},
        {"id": "t-echo-inkhud", "name": "LILYGO® T-Echo InkHUD", "manufacturer": "LILYGO"},
        
        # T-TWR Devices
        {"id": "t-deck", "name": "LILYGO® T-Deck", "manufacturer": "LILYGO"},
        {"id": "t-deck-tft", "name": "LILYGO® T-Deck TFT", "manufacturer": "LILYGO"},
        
        # TLORA Devices
        {"id": "tlora-v1", "name": "LILYGO® TLORA v1", "manufacturer": "LILYGO"},
        {"id": "tlora_v1_3", "name": "LILYGO® TLORA v1.3", "manufacturer": "LILYGO"},
        {"id": "tlora-v2", "name": "LILYGO® TLORA v2", "manufacturer": "LILYGO"},
        {"id": "tlora-v2-1-1_6", "name": "LILYGO® TLORA v2.1-1.6", "manufacturer": "LILYGO"},
        {"id": "tlora-v2-1-1_8", "name": "LILYGO® TLORA v2.1-1.8", "manufacturer": "LILYGO"},
        {"id": "tlora-t3s3-v1", "name": "LILYGO® TLORA T3S3 v1", "manufacturer": "LILYGO"},
        {"id": "tlora-t3s3-epaper", "name": "LILYGO® TLORA T3S3 ePaper", "manufacturer": "LILYGO"},
        
        # Heltec Devices
        {"id": "heltec-v1", "name": "Heltec Wireless Stick v1", "manufacturer": "Heltec"},
        {"id": "heltec-v2_0", "name": "Heltec Wireless Stick v2.0", "manufacturer": "Heltec"},
        {"id": "heltec-v2_1", "name": "Heltec Wireless Stick v2.1", "manufacturer": "Heltec"},
        {"id": "heltec-v3", "name": "Heltec Wireless Stick v3", "manufacturer": "Heltec"},
        {"id": "heltec-wireless-paper", "name": "Heltec Wireless Paper", "manufacturer": "Heltec"},
        {"id": "heltec-wireless-tracker", "name": "Heltec Wireless Tracker", "manufacturer": "Heltec"},
        
        # RAK Devices
        {"id": "rak4631", "name": "RAK Wireless WisBlock RAK4631", "manufacturer": "RAK"},
        {"id": "rak11200", "name": "RAK Wireless WisBlock RAK11200", "manufacturer": "RAK"},
        {"id": "rak11310", "name": "RAK Wireless WisBlock RAK11310", "manufacturer": "RAK"},
        
        # Nano Devices
        {"id": "nano-g1", "name": "Nano G1", "manufacturer": "Meshtastic"},
        {"id": "nano-g1-explorer", "name": "Nano G1 Explorer Edition", "manufacturer": "Meshtastic"},
        {"id": "nano-g2-ultra", "name": "Nano G2 Ultra", "manufacturer": "Meshtastic"},
        
        # ESP32 Boards
        {"id": "ESP32-S3-Pico", "name": "ESP32-S3-Pico", "manufacturer": "Espressif"},
        
        # Tracker Devices
        {"id": "tracker-t1000-e", "name": "Tracker T1000E", "manufacturer": "Meshtastic"},
        {"id": "picomputer-s3", "name": "Pi Computer S3", "manufacturer": "Meshtastic"},
        
        # Station Devices
        {"id": "station-g1", "name": "Meshtastic Station G1", "manufacturer": "Meshtastic"},
        {"id": "station-g2", "name": "Meshtastic Station G2", "manufacturer": "Meshtastic"},
        
        # DIY Boards
        {"id": "meshtastic-diy-v1", "name": "Meshtastic DIY v1", "manufacturer": "DIY"},
        {"id": "meshtastic-diy-v1_1", "name": "Meshtastic DIY v1.1", "manufacturer": "DIY"},
        
        # Custom & Private Label Devices
        {"id": "tbeam-s3", "name": "T-Beam S3", "manufacturer": "Custom"}
    ]
    
    # Group variants by manufacturer for the template
    manufacturers = {}
    for variant in variants:
        mfg = variant["manufacturer"]
        if mfg not in manufacturers:
            manufacturers[mfg] = []
        manufacturers[mfg].append(variant)
    
    return render_template('firmware_builder.html', variants=variants, manufacturers=manufacturers)

@app.route('/build-firmware', methods=['POST'])
def build_firmware():
    """Build firmware with the provided configuration"""
    build_id = f'build_{int(time.time())}'
    build_dir = os.path.join(TEMP_DIR, build_id)
    
    try:
        # Create build directory
        os.makedirs(build_dir, exist_ok=True)
        
        # Get device variant
        variant = request.form.get('variant')
        
        # Get custom filename if provided
        custom_filename = request.form.get('custom_filename', '').strip()
        
        # Determine the source of the config
        config_source = request.form.get('config_source', 'upload')
        
        print(f"Build firmware request - variant: {variant}, source: {config_source}")
        
        # Path for the userPrefs file
        prefs_path = os.path.join(build_dir, 'userPrefs.jsonc')
        
        if config_source == 'current':
            # Get the configuration from form data
            config_json = request.form.get('config_json')
            
            if not config_json:
                # Try to get it from localStorage via a hidden field
                stored_config = request.form.get('stored_config')
                if stored_config:
                    config_json = stored_config
                else:
                    return jsonify({'success': False, 'error': 'No configuration data provided'})
            
            try:
                # Parse the JSON config
                config_data = json.loads(config_json)
                
                # Generate JSONC content
                jsonc_content = generate_jsonc(config_data)
                
                # Write to file
                with open(prefs_path, 'w') as f:
                    f.write(jsonc_content)
                
                print(f"Using JSON configuration data, created file at {prefs_path}")
            except Exception as e:
                print(f"Error processing JSON config: {str(e)}")
                return jsonify({'success': False, 'error': f'Invalid configuration: {str(e)}'})
        else:
            # Check if a userPrefs file was uploaded
            if 'userPrefs' not in request.files:
                print("No userPrefs file in request")
                return jsonify({'success': False, 'error': 'No userPrefs file uploaded'})
                
            user_prefs_file = request.files['userPrefs']
            
            if user_prefs_file.filename == '':
                print("Empty filename for userPrefs")
                return jsonify({'success': False, 'error': 'No file selected'})
                
            # Save the uploaded file
            user_prefs_file.save(prefs_path)
        
        # Now build the firmware with PlatformIO
        print(f"Starting firmware build for variant {variant}")
        
        # Call the build function that already exists
        result = build_firmware_with_pio(variant, prefs_path, build_dir)
        
        if result['success']:
            print(f"Build successful, firmware at {result['firmware_path']}")
            
            # Generate download URL with variant info and custom filename
            download_url = f'/download-firmware/{build_id}?variant={variant}'
            if custom_filename:
                # Add the custom filename to the URL (will be sanitized on download)
                download_url += f'&filename={custom_filename}'
            
            return jsonify({
                'success': True,
                'message': 'Firmware built successfully',
                'download_url': download_url,
                'build_id': build_id
            })
        else:
            print(f"Build failed: {result['error']}")
            return jsonify({
                'success': False,
                'error': f"Build failed: {result['error']}"
            })
        
    except Exception as e:
        print(f"Build firmware error: {str(e)}")
        
        # Clean up the build directory if it exists
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir, ignore_errors=True)
            
        return jsonify({'success': False, 'error': str(e)})

def warm_build_cache(firmware_dir, variant):
    """Check if build cache is warm"""
    try:
        print(f"Checking build cache for {variant}")
        
        # Check if cache is already warm (build directory exists with objects)
        build_variant_dir = os.path.join(firmware_dir, '.pio', 'build', variant)
        if os.path.exists(build_variant_dir):
            obj_files = [f for f in os.listdir(build_variant_dir) if f.endswith('.o')]
            if len(obj_files) > 10:  # If we have many object files, cache is warm
                print(f"Build cache is warm for {variant} ({len(obj_files)} object files)")
                return True
            else:
                print(f"Build cache is cold for {variant} ({len(obj_files)} object files)")
                return False
        else:
            print(f"No build cache found for {variant}")
            return False
            
    except Exception as e:
        print(f"Cache check error (non-critical): {e}")
        return False

def ensure_dependencies_cached(firmware_dir, variant):
    """Pre-install dependencies to speed up builds"""
    try:
        print(f"Ensuring dependencies are cached for {variant}")
        
        # Check if dependencies are already installed for this variant
        libdeps_dir = os.path.join(firmware_dir, '.pio', 'libdeps', variant)
        if os.path.exists(libdeps_dir) and os.listdir(libdeps_dir):
            lib_count = len([d for d in os.listdir(libdeps_dir) if os.path.isdir(os.path.join(libdeps_dir, d))])
            print(f"Dependencies already cached for {variant} ({lib_count} libraries)")
            return True
            
        # Install dependencies only (no compilation) with aggressive caching
        cmd = f"cd {firmware_dir} && pio pkg install -e {variant} --silent"
        print(f"Installing dependencies: {cmd}")
        
        # Set up aggressive caching environment
        env = os.environ.copy()
        env['PLATFORMIO_LIBDEPS_CACHE_DIR'] = os.path.join(firmware_dir, '.pio', 'libdeps_cache')
        env['PLATFORMIO_FORCE_COLOR'] = 'false'
        env['PLATFORMIO_NO_ANSI'] = '1'
        
        process = subprocess.Popen(
            cmd, 
            shell=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            env=env
        )
        stdout, stderr = process.communicate(timeout=120)  # 2 minute timeout
        
        if process.returncode != 0:
            print(f"Warning: Dependency installation failed: {stderr.decode('utf-8')}")
            return False
        else:
            print(f"Dependencies cached successfully for {variant}")
            return True
            
    except subprocess.TimeoutExpired:
        print(f"Warning: Dependency installation timed out")
        process.kill()
        return False
    except Exception as e:
        print(f"Warning: Could not cache dependencies: {e}")
        return False

def build_firmware_with_pio(variant, prefs_path, build_dir, fast_build=False):
    """Build firmware using PlatformIO with optimizations"""
    build_start_time = time.time()
    timing = {}
    
    try:
        print(f"Starting PlatformIO build for variant: {variant}")
        
        # Get the firmware directory
        firmware_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'firmware')
        
        # Time dependency caching
        deps_start = time.time()
        ensure_dependencies_cached(firmware_dir, variant)
        timing['dependencies'] = time.time() - deps_start
        
        # Time file operations
        file_ops_start = time.time()
        # Copy userPrefs.jsonc to BOTH possible locations the build might expect
        # 1. To the configs directory (where we've been putting it)
        target_prefs_dir = os.path.join(firmware_dir, 'configs')
        os.makedirs(target_prefs_dir, exist_ok=True)
        shutil.copy(prefs_path, os.path.join(target_prefs_dir, 'userPrefs.jsonc'))
        
        # 2. Also copy to the firmware root directory (where the build script is looking for it)
        shutil.copy(prefs_path, os.path.join(firmware_dir, 'userPrefs.jsonc'))
        
        print(f"Configuration copied to {target_prefs_dir} and firmware root")
        timing['file_operations'] = time.time() - file_ops_start
        
        # Time cache warming (after userPrefs is in place)
        cache_start = time.time()
        warm_build_cache(firmware_dir, variant)
        timing['cache_warming'] = time.time() - cache_start
        
        # Time the actual compilation
        compile_start = time.time()
        
        # Optimize build with aggressive caching and parallelization
        import multiprocessing
        cpu_count = multiprocessing.cpu_count()
        jobs = min(cpu_count, 12)  # Increase job limit for faster builds
        
        # Build command with aggressive optimizations:
        # --jobs: Use more CPU cores
        # --silent: Reduce output for faster builds
        # --disable-auto-clean: Don't clean before build (incremental builds)
        cmd = f"cd {firmware_dir} && pio run -e {variant} --jobs {jobs} --silent --disable-auto-clean"
        print(f"Executing optimized build command: {cmd}")
        
        # Run the build process with optimized settings
        env = os.environ.copy()
        # Set PlatformIO environment variables for faster builds
        env['PLATFORMIO_BUILD_CACHE_DIR'] = os.path.join(firmware_dir, '.pio', 'build_cache')
        env['PLATFORMIO_LIBDEPS_CACHE_DIR'] = os.path.join(firmware_dir, '.pio', 'libdeps_cache')
        env['PLATFORMIO_CORE_DIR'] = os.path.join(firmware_dir, '.pio', 'core')
        
        # Aggressive compiler optimizations
        env['PLATFORMIO_BUILD_FLAGS'] = '-O2 -DNDEBUG -ffast-math -fno-exceptions'
        env['PLATFORMIO_UPLOAD_FLAGS'] = '--disable-flushing'
        
        # Memory and I/O optimizations
        env['PLATFORMIO_FORCE_COLOR'] = 'false'  # Disable color output for speed
        env['PLATFORMIO_NO_ANSI'] = '1'  # Disable ANSI codes
        
        # Use faster linker if available
        env['PLATFORMIO_BUILD_UNFLAGS'] = '-fno-lto'  # Remove flags that slow down builds
        
        # Enable ccache if available for even faster compilation
        if shutil.which('ccache'):
            env['CC'] = 'ccache gcc'
            env['CXX'] = 'ccache g++'
            print("Using ccache for faster compilation")
        
        # Try to use RAM disk for temporary files if available
        ram_disk_paths = ['/dev/shm', '/tmp']
        temp_build_dir = None
        for ram_path in ram_disk_paths:
            if os.path.exists(ram_path) and os.access(ram_path, os.W_OK):
                temp_build_dir = os.path.join(ram_path, f'pio_build_{variant}_{int(time.time())}')
                env['PLATFORMIO_BUILD_DIR'] = temp_build_dir
                print(f"Using RAM disk for build: {temp_build_dir}")
                break
        
        process = subprocess.Popen(
            cmd, 
            shell=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            env=env
        )
        stdout, stderr = process.communicate()
        
        # Print the command output for debugging (only on error or if verbose)
        stdout_text = stdout.decode('utf-8')
        stderr_text = stderr.decode('utf-8')
        
        if process.returncode != 0:
            print(f"Build failed. stdout: {stdout_text}")
            print(f"Build stderr: {stderr_text}")
            return {
                'success': False,
                'error': f"Build failed: {stderr_text}"
            }
        else:
            # Only print summary on success to reduce noise
            print(f"Build completed successfully for {variant}")
            
        timing['compilation'] = time.time() - compile_start
        
        # Time file discovery and copying
        post_build_start = time.time()
        
        # Find the built firmware file - check both RAM disk and default locations
        firmware_path = None
        
        # First check if we used a RAM disk build
        if temp_build_dir and os.path.exists(temp_build_dir):
            ram_firmware_path = os.path.join(temp_build_dir, variant, 'firmware.uf2')
            if os.path.exists(ram_firmware_path):
                firmware_path = ram_firmware_path
                print(f"Found firmware in RAM disk: {firmware_path}")
        
        # Fallback to default location
        if not firmware_path:
            default_firmware_path = os.path.join(firmware_dir, '.pio', 'build', variant, 'firmware.uf2')
            if os.path.exists(default_firmware_path):
                firmware_path = default_firmware_path
                print(f"Found firmware in default location: {firmware_path}")
        
        if not firmware_path:
            return {
                'success': False,
                'error': "Build completed but firmware file not found in any location"
            }
            
        print(f"Using firmware file: {firmware_path}")
        
        # IMPORTANT: Copy firmware to build directory BEFORE cleanup
        # The original firmware_path will be cleaned up to prevent PSK leakage
        dest_firmware_path = os.path.join(build_dir, 'firmware.uf2')
        shutil.copy(firmware_path, dest_firmware_path)
        print(f"Copied firmware to secure build directory: {dest_firmware_path}")
        
        timing['post_build'] = time.time() - post_build_start
        
        # Time cleanup operations
        cleanup_start = time.time()
        
        # Clean up sensitive files after successful build and copy
        try:
            # Remove userPrefs from configs directory
            config_prefs_path = os.path.join(firmware_dir, 'configs', 'userPrefs.jsonc')
            if os.path.exists(config_prefs_path):
                os.remove(config_prefs_path)
                print(f"Cleaned up userPrefs from configs directory")
            
            # Remove userPrefs from firmware root
            root_prefs_path = os.path.join(firmware_dir, 'userPrefs.jsonc')
            if os.path.exists(root_prefs_path):
                os.remove(root_prefs_path)
                print(f"Cleaned up userPrefs from firmware root")
                
            # CRITICAL: Remove the UF2 file containing PSK data to prevent leakage
            if os.path.exists(firmware_path):
                os.remove(firmware_path)
                print(f"Cleaned up UF2 file with PSK data: {firmware_path}")
                
            # Also clean up RAM disk build directory if it was used
            if temp_build_dir and os.path.exists(temp_build_dir):
                shutil.rmtree(temp_build_dir, ignore_errors=True)
                print(f"Cleaned up RAM disk build directory: {temp_build_dir}")
                
        except Exception as cleanup_error:
            print(f"Warning: Could not clean up sensitive files: {cleanup_error}")
        
        timing['cleanup'] = time.time() - cleanup_start
        
        # Report detailed build time breakdown
        total_time = time.time() - build_start_time
        print(f"Build completed in {total_time:.1f} seconds")
        print(f"Timing breakdown:")
        print(f"  Dependencies: {timing.get('dependencies', 0):.1f}s")
        print(f"  Cache warming: {timing.get('cache_warming', 0):.1f}s") 
        print(f"  File operations: {timing.get('file_operations', 0):.1f}s")
        print(f"  Compilation: {timing.get('compilation', 0):.1f}s")
        print(f"  Post-build: {timing.get('post_build', 0):.1f}s")
        print(f"  Cleanup: {timing.get('cleanup', 0):.1f}s")
        
        return {
            'success': True,
            'firmware_path': dest_firmware_path,  # Return path to copied firmware
            'timing': timing,
            'total_time': total_time
        }
        
    except Exception as e:
        print(f"Exception in build_firmware_with_pio: {str(e)}")
        
        # Clean up sensitive userPrefs files even on error
        try:
            firmware_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'firmware')
            config_prefs_path = os.path.join(firmware_dir, 'configs', 'userPrefs.jsonc')
            root_prefs_path = os.path.join(firmware_dir, 'userPrefs.jsonc')
            
            if os.path.exists(config_prefs_path):
                os.remove(config_prefs_path)
                print(f"Cleaned up userPrefs from configs directory (error case)")
            if os.path.exists(root_prefs_path):
                os.remove(root_prefs_path)
                print(f"Cleaned up userPrefs from firmware root (error case)")
                
            # Also clean up any UF2 files that might contain PSK data
            pio_build_dir = os.path.join(firmware_dir, '.pio', 'build')
            if os.path.exists(pio_build_dir):
                for variant_dir in os.listdir(pio_build_dir):
                    variant_path = os.path.join(pio_build_dir, variant_dir)
                    if os.path.isdir(variant_path):
                        uf2_path = os.path.join(variant_path, 'firmware.uf2')
                        if os.path.exists(uf2_path):
                            os.remove(uf2_path)
                            print(f"Cleaned up UF2 file from {variant_dir} (error case)")
            
            # Clean up RAM disk build directory if it was used
            if 'temp_build_dir' in locals() and temp_build_dir and os.path.exists(temp_build_dir):
                shutil.rmtree(temp_build_dir, ignore_errors=True)
                print(f"Cleaned up RAM disk build directory (error case): {temp_build_dir}")
                
        except Exception as cleanup_error:
            print(f"Warning: Could not clean up sensitive files on error: {cleanup_error}")
        
        return {
            'success': False,
            'error': str(e)
        }

@app.route('/update-firmware', methods=['POST'])
def update_firmware_route():
    try:
        # Check if the user has permission (could be admin-only in production)
        # This is a simple example - you'd want more security in production
        if request.form.get('admin_key') != ADMIN_PASSWORD:
            return jsonify({'success': False, 'error': 'Unauthorized'})
        
        # Run the firmware update in a background thread to not block the request
        import threading
        update_thread = threading.Thread(target=update_firmware)
        update_thread.start()
        
        return jsonify({
            'success': True, 
            'message': 'Firmware update started. This may take several minutes.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin')
def admin_page():
    return render_template('admin.html', title="Admin Dashboard")

@app.route('/system-info')
def system_info():
    try:
        # Get firmware version info
        version_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "firmware_version.txt")
        firmware_version = "Not installed"
        last_updated = "Never"
        
        if os.path.exists(version_file):
            with open(version_file, "r") as f:
                lines = f.readlines()
                if len(lines) >= 2:
                    firmware_version = lines[0].strip().replace("Version: ", "")
                    last_updated = lines[1].strip().replace("Updated: ", "")
        
        return jsonify({
            'success': True,
            'firmware_version': firmware_version,
            'last_updated': last_updated
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download-firmware/<build_id>', methods=['GET'])
def download_firmware(build_id):
    """Download the built firmware file"""
    try:
        # Build path to the firmware file
        build_dir = os.path.join(TEMP_DIR, build_id)
        firmware_path = os.path.join(build_dir, 'firmware.uf2')
        
        # Check if this is an actual build and the firmware exists
        if not os.path.isdir(build_dir) or not os.path.isfile(firmware_path):
            return jsonify({'success': False, 'error': 'Firmware not found'}), 404
        
        variant = request.args.get('variant', 'unknown')
        custom_filename = request.args.get('filename', '')
        
        # Sanitize custom filename if provided
        if custom_filename:
            # Remove any unsafe characters and ensure it ends with .uf2
            import re
            custom_filename = re.sub(r'[^\w\-\.]', '_', custom_filename)
            if not custom_filename.lower().endswith('.uf2'):
                custom_filename += '.uf2'
            download_name = custom_filename
        else:
            # Use default naming convention
            download_name = f'meshtastic_{variant}_firmware.uf2'
        
        # Set cache control headers
        response = send_file(
            firmware_path,
            as_attachment=True,
            download_name=download_name,
            mimetype='application/octet-stream'
        )
        
        # Add cache control headers to the response
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        
        # Schedule cleanup of the build directory after successful download
        import threading
        def cleanup_after_delay():
            import time
            time.sleep(5)  # Wait 5 seconds to ensure download completes
            try:
                if os.path.exists(build_dir):
                    shutil.rmtree(build_dir, ignore_errors=True)
                    print(f"Cleaned up build directory: {build_dir}")
            except Exception as e:
                print(f"Error cleaning up build directory {build_dir}: {e}")
        
        cleanup_thread = threading.Thread(target=cleanup_after_delay)
        cleanup_thread.daemon = True
        cleanup_thread.start()
        
        return response
        
    except Exception as e:
        print(f"Download firmware error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

def cleanup_old_builds():
    """Clean up build directories older than 1 hour"""
    try:
        if not os.path.exists(TEMP_DIR):
            return
            
        current_time = time.time()
        for item in os.listdir(TEMP_DIR):
            if item.startswith('build_'):
                build_path = os.path.join(TEMP_DIR, item)
                if os.path.isdir(build_path):
                    # Extract timestamp from build directory name
                    try:
                        timestamp = int(item.replace('build_', ''))
                        # If older than 1 hour (3600 seconds), remove it
                        if current_time - timestamp > 3600:
                            shutil.rmtree(build_path, ignore_errors=True)
                            print(f"Cleaned up old build directory: {build_path}")
                    except (ValueError, OSError) as e:
                        print(f"Error processing build directory {item}: {e}")
    except Exception as e:
        print(f"Error during cleanup_old_builds: {e}")

def cleanup_userprefs_files():
    """Clean up any leftover userPrefs.jsonc files containing sensitive PSK data"""
    try:
        firmware_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'firmware')
        
        # Clean up from configs directory
        config_prefs_path = os.path.join(firmware_dir, 'configs', 'userPrefs.jsonc')
        if os.path.exists(config_prefs_path):
            os.remove(config_prefs_path)
            print(f"Cleaned up leftover userPrefs from configs directory")
        
        # Clean up from firmware root
        root_prefs_path = os.path.join(firmware_dir, 'userPrefs.jsonc')
        if os.path.exists(root_prefs_path):
            os.remove(root_prefs_path)
            print(f"Cleaned up leftover userPrefs from firmware root")
            
    except Exception as e:
        print(f"Error during cleanup_userprefs_files: {e}")

def cleanup_uf2_files():
    """Clean up any leftover UF2 firmware files containing sensitive PSK data"""
    try:
        firmware_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'firmware')
        pio_build_dir = os.path.join(firmware_dir, '.pio', 'build')
        
        if not os.path.exists(pio_build_dir):
            return
            
        cleaned_count = 0
        for variant_dir in os.listdir(pio_build_dir):
            variant_path = os.path.join(pio_build_dir, variant_dir)
            if os.path.isdir(variant_path):
                uf2_path = os.path.join(variant_path, 'firmware.uf2')
                if os.path.exists(uf2_path):
                    os.remove(uf2_path)
                    cleaned_count += 1
                    print(f"Cleaned up UF2 file from {variant_dir}")
                    
        if cleaned_count > 0:
            print(f"Cleaned up {cleaned_count} UF2 files containing PSK data")
            
    except Exception as e:
        print(f"Error during cleanup_uf2_files: {e}")

def full_cleanup():
    """Run all cleanup functions"""
    cleanup_old_builds()
    cleanup_userprefs_files()
    cleanup_uf2_files()

# Add startup cleanup when the app starts
full_cleanup()

# Schedule periodic cleanup every 30 minutes
import threading
def periodic_cleanup():
    while True:
        time.sleep(1800)  # 30 minutes
        full_cleanup()

cleanup_thread = threading.Thread(target=periodic_cleanup)
cleanup_thread.daemon = True
cleanup_thread.start()

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    """Manually trigger cleanup of old build directories and sensitive files"""
    try:
        # Check if the user has permission (could be admin-only in production)
        if request.form.get('admin_key') != ADMIN_PASSWORD:
            return jsonify({'success': False, 'error': 'Unauthorized'})
        
        # Count directories before cleanup
        build_dirs_before = len([d for d in os.listdir(TEMP_DIR) if d.startswith('build_')]) if os.path.exists(TEMP_DIR) else 0
        
        # Run full cleanup (builds + userPrefs)
        full_cleanup()
        
        # Count directories after cleanup
        build_dirs_after = len([d for d in os.listdir(TEMP_DIR) if d.startswith('build_')]) if os.path.exists(TEMP_DIR) else 0
        
        cleaned_count = build_dirs_before - build_dirs_after
        
        return jsonify({
            'success': True,
            'message': f'Cleanup complete. Removed {cleaned_count} old build directories and any leftover userPrefs/UF2 files with PSK data.',
            'builds_before': build_dirs_before,
            'builds_after': build_dirs_after
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    # Get host and port from environment variables for container compatibility
    host = os.environ.get('FLASK_HOST', '0.0.0.0')
    port = int(os.environ.get('FLASK_PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    
    print(f"Starting MTFWBuilder on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug) 