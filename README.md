# Meshtastic Configuration Generator (UserPrefs Only)

[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/flask-2.0+-green.svg)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

A lightweight, user-friendly web interface for generating Meshtastic device configurations. This version focuses solely on creating `userPrefs.jsonc` files for manual flashing or use with other firmware builders.

## 🚀 Features

### Configuration Generator
- **Intuitive Web Interface**: Clean, modern UI with Bootstrap styling and dark mode support
- **Complete Configuration Options**: 
  - Device information (name, owner details, timezone)
  - Multi-channel configuration with custom PSK generation
  - LoRa radio settings (region, modem preset, channel selection)
  - GPS configuration with smart positioning
  - Network settings (WiFi, MQTT)
  - Device role configuration
  - Advanced device settings
- **Real-time Preview**: See your configuration in JSONC format before generating
- **Secure PSK Generation**: Cryptographically secure pre-shared key generation
- **Timezone Auto-detection**: Automatically detects and sets your local timezone
- **Lightweight**: No firmware building, PlatformIO, or admin overhead

## 📋 Requirements

- Python 3.8 or higher
- 512MB+ RAM 
- 100MB+ disk space

## 🛠️ Installation

### Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/Crank-Git/MTFWBuilder.git
   cd MTFWBuilder
   git checkout userprefs-only
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the application**
   ```bash
   python app.py
   ```

5. **Open your browser** and navigate to `http://localhost:5000`

## 🎯 Usage

### Generating Configuration Files

1. **Basic Information**: Enter your device name, owner information, and timezone
2. **Channel Configuration**: Set up your mesh channels with custom names and encryption
3. **LoRa Settings**: Configure your radio parameters for your region
4. **GPS Configuration**: Set up position reporting and smart positioning
5. **Network Settings**: Configure WiFi and MQTT if needed
6. **Device Role**: Select your device's role in the mesh network
7. **Preview & Download**: Review your configuration and download the `userPrefs.jsonc` file

### Using Your Configuration

The generated `userPrefs.jsonc` file can be used with:

1. **Meshtastic Web Flasher**: Upload to [flasher.meshtastic.org](https://flasher.meshtastic.org/)
2. **Meshtastic CLI**: Use with `meshtastic --set-config userPrefs.jsonc`
3. **Manual Firmware Building**: Include in your own PlatformIO builds
4. **Other Tools**: Any tool that accepts Meshtastic configuration files

## 🏗️ Architecture

```
MTFWBuilder-UserPrefs/
├── app.py                 # Lightweight Flask application
├── requirements.txt       # Minimal Python dependencies
├── static/               # CSS, JavaScript, and assets
│   ├── css/             # Stylesheets
│   └── js/              # JavaScript modules
├── templates/           # Jinja2 HTML templates
│   └── includes/        # Reusable template components
└── utils/               # Utility modules
    └── jsonc_generator.py    # Configuration file generator
```

## 🧪 Development

### Setting Up Development Environment

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make your changes
4. Submit a pull request

## 🐛 Troubleshooting

### Common Issues

**Configuration not generating**
- Verify all required fields are filled
- Check that PSK format is correct (32-byte hex array)
- Try the preview function to debug issues

**JavaScript errors**
- Ensure browser supports modern JavaScript features
- Check browser console for specific error messages
- Try clearing browser cache

**File download issues**
- Check browser download settings
- Ensure popup blockers aren't interfering
- Try different browsers if issues persist

### Getting Help

1. Check the [Issues](https://github.com/Crank-Git/MTFWBuilder/issues) page
2. Review [Meshtastic Documentation](https://meshtastic.org/docs/)
3. Join the [Meshtastic Discord](https://discord.gg/ktMAKGBnBs)

## 🤝 Contributing

We welcome contributions! Here's how you can help:

### Types of Contributions

- 🐛 **Bug Reports**: Help us identify and fix issues
- 💡 **Feature Requests**: Suggest new configuration options
- 📖 **Documentation**: Improve our docs and examples
- 🔧 **Code Contributions**: Submit bug fixes and new features
- 🎨 **UI/UX Improvements**: Make the interface even better

### Quick Contribution Guide

1. Fork the project
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

### Development Setup

1. **Fork and clone** the repository
2. **Create a virtual environment**: `python -m venv venv`
3. **Activate it**: `source venv/bin/activate` (Linux/Mac) or `venv\Scripts\activate` (Windows)
4. **Install dependencies**: `pip install -r requirements.txt`
5. **Make your changes** and test thoroughly
6. **Submit a pull request** with a clear description

## 📄 License

**MIT License**

Copyright (c) 2025 Meshtastic Configuration Generator Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## 🙏 Acknowledgments

- [Meshtastic Project](https://meshtastic.org/) for the amazing mesh networking platform
- [Bootstrap](https://getbootstrap.com/) for the beautiful UI components
- All contributors who help make this project better

## 📊 Project Status

- ✅ UserPrefs Configuration Generation
- ✅ Real-time Preview
- ✅ Secure PSK Generation  
- ✅ Lightweight Web Interface
- ✅ Multi-browser Support

## 🔗 Related Projects

- [Meshtastic](https://github.com/meshtastic/Meshtastic-device) - The main Meshtastic firmware
- [Meshtastic Web](https://github.com/meshtastic/web) - Official web interface
- [Meshtastic Python](https://github.com/meshtastic/Meshtastic-python) - Python API
- [MTFWBuilder (Full Version)](https://github.com/Crank-Git/MTFWBuilder/tree/main) - Complete version with firmware building

---

**Star ⭐ this repo if you find it useful!**