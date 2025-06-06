"""
Meshtastic Configuration Generator (UserPrefs Only)

A lightweight Flask web application for generating Meshtastic device configurations.
This version only generates userPrefs.jsonc files - no firmware building capability.

Author: Meshtastic Configuration Generator Contributors
License: MIT
"""

from flask import Flask, render_template, request, jsonify, make_response
from utils.jsonc_generator import generate_jsonc
import json
from typing import Dict, Any, Tuple

app = Flask(__name__)

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

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000) 