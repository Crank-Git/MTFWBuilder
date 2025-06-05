document.addEventListener('DOMContentLoaded', function() {
    // Dark mode toggle
    const darkModeToggle = document.getElementById('darkModeToggle');
    const htmlElement = document.documentElement;
    
    // Check for saved theme preference or use dark mode as default
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme) {
        htmlElement.setAttribute('data-bs-theme', savedTheme);
        updateDarkModeIcon(savedTheme);
    } else {
        // Default to dark mode
        htmlElement.setAttribute('data-bs-theme', 'dark');
        updateDarkModeIcon('dark');
    }
    
    // Toggle dark/light mode
    if (darkModeToggle) {
        darkModeToggle.addEventListener('click', function() {
            const currentTheme = htmlElement.getAttribute('data-bs-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            
            htmlElement.setAttribute('data-bs-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            updateDarkModeIcon(newTheme);
        });
    }
    
    function updateDarkModeIcon(theme) {
        if (!darkModeToggle) return;
        
        const icon = darkModeToggle.querySelector('i');
        if (theme === 'dark') {
            icon.classList.remove('bi-moon-fill');
            icon.classList.add('bi-sun-fill');
        } else {
            icon.classList.remove('bi-sun-fill');
            icon.classList.add('bi-moon-fill');
        }
    }
    
    const configForm = document.getElementById('configForm');
    const previewButton = document.getElementById('previewButton');
    const downloadButton = document.getElementById('downloadButton');
    const previewContainer = document.getElementById('preview-container');
    const channelsToWrite = document.getElementById('channelsToWrite');
    const loraEnabled = document.getElementById('loraEnabled');
    const loraConfig = document.getElementById('loraConfig');
    const gpsEnabled = document.getElementById('gpsEnabled');
    const gpsConfig = document.getElementById('gpsConfig');
    const fixedPositionEnabled = document.getElementById('fixedPositionEnabled');
    const fixedPositionFields = document.getElementById('fixedPositionFields');
    const networkEnabled = document.getElementById('networkEnabled');
    const networkConfig = document.getElementById('networkConfig');
    const mqttEnabled = document.getElementById('mqttEnabled');
    const mqttConfig = document.getElementById('mqttConfig');
    const loader = document.getElementById('loader');
    
    // Toggle sections based on checkboxes
    if (loraEnabled) {
        loraEnabled.addEventListener('change', function() {
            loraConfig.style.display = this.checked ? 'block' : 'none';
        });
    }
    
    if (gpsEnabled) {
        gpsEnabled.addEventListener('change', function() {
            gpsConfig.style.display = this.checked ? 'block' : 'none';
        });
    }
    
    if (fixedPositionEnabled) {
        fixedPositionEnabled.addEventListener('change', function() {
            fixedPositionFields.style.display = this.checked ? 'block' : 'none';
        });
    }
    
    if (networkEnabled) {
        networkEnabled.addEventListener('change', function() {
            networkConfig.style.display = this.checked ? 'block' : 'none';
        });
    }
    
    if (mqttEnabled) {
        mqttEnabled.addEventListener('change', function() {
            mqttConfig.style.display = this.checked ? 'block' : 'none';
        });
    }
    
    // Handle channel tabs visibility
    if (channelsToWrite) {
        channelsToWrite.addEventListener('change', function() {
            const numChannels = parseInt(this.value);
            const tabs = document.querySelectorAll('#channelTabsList .nav-item');
            
            tabs.forEach((tab, index) => {
                if (index < numChannels) {
                    tab.style.display = 'block';
                } else {
                    tab.style.display = 'none';
                }
            });
            
            // Make first tab active if it exists
            if (numChannels > 0 && !document.querySelector('#channelTabsList .nav-link.active')) {
                document.querySelector('#channelTabsList .nav-link').classList.add('active');
                document.querySelector('#channelTabsContent .tab-pane').classList.add('show', 'active');
            }
        });
        
        // Trigger change event to set initial state
        channelsToWrite.dispatchEvent(new Event('change'));
    }
    
    // Preview button click handler
    if (previewButton) {
        previewButton.addEventListener('click', function() {
            const formData = getFormData();
            
            loader.style.display = 'block';
            
            fetch('/preview', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData)
            })
            .then(response => response.json())
            .then(data => {
                loader.style.display = 'none';
                
                if (data.success) {
                    previewContainer.textContent = data.content;
                } else {
                    previewContainer.textContent = JSON.stringify(data, null, 2);
                }
            })
            .catch(error => {
                loader.style.display = 'none';
                console.error('Error:', error);
                previewContainer.textContent = 'An error occurred during preview generation.';
            });
        });
    }
    
    // Download button click handler
    if (downloadButton) {
        downloadButton.addEventListener('click', function() {
            const formData = getFormData();
            
            loader.style.display = 'block';
            
            // Send JSON data to the server
            fetch('/download', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData)
            })
            .then(response => {
                loader.style.display = 'none';
                
                if (!response.ok) {
                    throw new Error('Network response was not ok');
                }
                
                // Get the response as a blob
                return response.blob();
            })
            .then(blob => {
                // Create a link to download the blob
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = 'userPrefs.jsonc';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
            })
            .catch(error => {
                loader.style.display = 'none';
                console.error('Error:', error);
                alert('An error occurred while downloading the file.');
            });
        });
    }
    
    // Helper function to get form data
    function getFormData() {
        const formData = {};
        const elements = configForm.elements;
        
        for (let i = 0; i < elements.length; i++) {
            const element = elements[i];
            
            if (element.name) {
                if (element.type === 'checkbox') {
                    formData[element.name] = element.checked ? "true" : "false";
                } else if (element.type === 'radio') {
                    if (element.checked) {
                        formData[element.name] = element.value;
                    }
                } else {
                    formData[element.name] = element.value;
                }
            }
        }
        
        console.log('Raw form data collected:', formData);
        return formData;
    }
    
    // Add event listeners to all PSK generator buttons
    const pskButtons = document.querySelectorAll('.generate-psk-btn');
    pskButtons.forEach(button => {
        button.addEventListener('click', function() {
            const targetId = this.getAttribute('data-target');
            const targetInput = document.getElementById(targetId);
            if (targetInput) {
                targetInput.value = generatePSK();
            }
        });
    });
});

// Function to generate random PSK
function generatePSK() {
    // Generate 32 random bytes (crypto.getRandomValues is more secure than Math.random)
    const bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    
    // Format as C-style hex array
    let pskString = "{ ";
    bytes.forEach((byte, index) => {
        pskString += "0x" + byte.toString(16).padStart(2, '0');
        if (index < bytes.length - 1) {
            pskString += ", ";
        }
    });
    pskString += " }";
    
    return pskString;
} 